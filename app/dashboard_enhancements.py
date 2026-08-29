from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .db import get_db
from .domain import D, aggregated_dashboard, money, payment_fee_rate, reconcile
from .models import (
    CommercialStatus,
    Company,
    Order,
    OrderItem,
    ProductionOrder,
    ProductionStatus,
    Product,
    Role,
    User,
)


def _remove_dashboard_route(app) -> None:
    for route in list(app.router.routes):
        if getattr(route, "path", None) == "/dashboard" and "GET" in getattr(route, "methods", set()):
            app.router.routes.remove(route)


def _most_profitable_product(db: Session, company_id: str) -> dict | None:
    company = db.get(Company, company_id)
    orders = db.scalars(
        select(Order)
        .options(
            selectinload(Order.items).selectinload(OrderItem.product),
            selectinload(Order.snapshots),
        )
        .where(
            Order.company_id == company_id,
            Order.commercial_status == CommercialStatus.CONFIRMED,
        )
    ).all()
    totals: dict[str, dict] = defaultdict(lambda: {"name": "", "result": Decimal("0"), "revenue": Decimal("0")})

    for order in orders:
        gross_total = sum((D(item.qty) * D(item.unit_price) for item in order.items), Decimal("0"))
        if gross_total <= 0:
            continue
        revenue_adjustment = D(order.freight_charged) - D(order.discount)
        order_revenue = gross_total + revenue_adjustment
        payment_fee = order_revenue * payment_fee_rate(company, order.expected_payment_method)
        shared_cost = D(order.company_freight_cost) + payment_fee
        snapshots = {snapshot.order_item_id: snapshot for snapshot in order.snapshots}

        for item in order.items:
            snapshot = snapshots.get(item.id)
            if not snapshot:
                continue
            gross = D(item.qty) * D(item.unit_price)
            share = gross / gross_total
            item_revenue = gross + (revenue_adjustment * share)
            item_cost = D(snapshot.total_cost) + (shared_cost * share)
            row = totals[item.product_id]
            row["name"] = item.product.name
            row["revenue"] += item_revenue
            row["result"] += item_revenue - item_cost

    if not totals:
        return None
    best = max(totals.values(), key=lambda row: row["result"])
    return {
        "name": best["name"],
        "result": money(best["result"]),
        "revenue": money(best["revenue"]),
    }


def install_dashboard_enhancements(main_module) -> None:
    app = main_module.app
    if getattr(app.state, "decision_dashboard_installed", False):
        return
    _remove_dashboard_route(app)

    def dashboard(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_user),
    ):
        kpis = aggregated_dashboard(db, user.company_id) if user.role == Role.MANAGER else None
        decision = None
        if user.role == Role.MANAGER:
            decision = {
                "profitable_product": _most_profitable_product(db, user.company_id),
                "blocked_productions": int(
                    db.scalar(
                        select(func.count())
                        .select_from(ProductionOrder)
                        .join(Order)
                        .where(
                            Order.company_id == user.company_id,
                            Order.commercial_status == CommercialStatus.CONFIRMED,
                            ProductionOrder.status == ProductionStatus.BLOCKED,
                        )
                    )
                    or 0
                ),
                "reconciliation_issues": reconcile(db, user.company_id),
            }

        productions = db.scalars(
            select(ProductionOrder)
            .join(Order)
            .where(
                Order.company_id == user.company_id,
                ProductionOrder.status.in_([
                    ProductionStatus.BLOCKED,
                    ProductionStatus.READY,
                    ProductionStatus.IN_PROGRESS,
                ]),
            )
            .order_by(Order.current_promised_at.asc().nulls_last())
            .limit(8)
        ).all()
        deliveries = db.scalars(
            select(Order)
            .where(
                Order.company_id == user.company_id,
                Order.commercial_status == CommercialStatus.CONFIRMED,
            )
            .order_by(Order.current_promised_at.asc().nulls_last())
            .limit(8)
        ).all()
        return main_module.render(
            request,
            "dashboard.html",
            user=user,
            kpis=kpis,
            decision=decision,
            productions=productions,
            deliveries=deliveries,
        )

    app.add_api_route(
        "/dashboard",
        dashboard,
        methods=["GET"],
        response_class=HTMLResponse,
        name="decision_dashboard",
    )
    app.state.decision_dashboard_installed = True
