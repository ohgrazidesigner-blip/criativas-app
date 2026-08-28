from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    AuditEvent,
    CommercialStatus,
    Company,
    CostSnapshot,
    DeliveryAttempt,
    Fulfillment,
    FulfillmentMethod,
    FulfillmentStatus,
    GoodsReceipt,
    GoodsReceiptItem,
    InventoryReservation,
    InventoryTransaction,
    InventoryTxType,
    Material,
    MaterialRequirement,
    Order,
    OrderItem,
    OutboxEvent,
    Payment,
    PaymentMethod,
    ProductionException,
    ProductionOrder,
    ProductionStatus,
    Purchase,
    PurchaseItem,
    Role,
    User,
)

MONEY = Decimal("0.01")
QTY = Decimal("0.001")


def D(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def money(value: Decimal) -> Decimal:
    return D(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def qty(value: Decimal) -> Decimal:
    return D(value).quantize(QTY, rounding=ROUND_HALF_UP)


@dataclass
class Economics:
    revenue: Decimal
    estimated_cost: Decimal | None
    result: Decimal | None
    margin: Decimal | None
    integrity: str
    materials: Decimal | None = None
    labor: Decimal | None = None
    fixed: Decimal | None = None
    expected_loss: Decimal | None = None
    company_freight: Decimal | None = None
    payment_fee: Decimal | None = None


class DomainError(ValueError):
    pass


def audit(db: Session, company_id: str, actor_user_id: str | None, event_type: str, entity_type: str, entity_id: str, details: dict | None = None):
    db.add(AuditEvent(
        company_id=company_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        details=json.dumps(details or {}, ensure_ascii=False),
    ))


def outbox(db: Session, company_id: str, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict | None = None):
    db.add(OutboxEvent(
        company_id=company_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=json.dumps(payload or {}, ensure_ascii=False),
    ))


def current_company(db: Session) -> Company:
    company = db.scalar(select(Company).limit(1))
    if not company:
        raise DomainError("Company not configured")
    return company


def next_order_number(db: Session) -> int:
    return int(db.scalar(select(func.max(Order.number))) or 2000) + 1


def next_purchase_number(db: Session) -> int:
    return int(db.scalar(select(func.max(Purchase.number))) or 140) + 1


def payment_fee_rate(company: Company, method: PaymentMethod) -> Decimal:
    if method == PaymentMethod.CARD:
        return D(company.card_fee_rate)
    if method == PaymentMethod.PIX:
        return D(company.pix_fee_rate)
    return Decimal("0")


def calculate_product_unit_cost(db: Session, company: Company, product) -> tuple[dict, bool]:
    items = [i for i in product.technical_items if i.version == max([x.version for x in product.technical_items] or [1])]
    materials_cost = Decimal("0")
    complete = bool(items)
    for item in items:
        if item.material.current_cost is None:
            complete = False
            continue
        materials_cost += D(item.qty) * D(item.material.current_cost)

    labor_cost = (D(product.labor_minutes) / Decimal("60")) * D(company.hourly_value)
    per_min = D(company.allocable_fixed_monthly) / D(company.productive_minutes_month or 1)
    fixed_cost = per_min * D(product.labor_minutes)
    base = materials_cost + labor_cost + fixed_cost
    loss_rate = D(product.expected_loss_rate)
    if loss_rate < 0 or loss_rate >= 1:
        raise DomainError("Expected loss rate must be >= 0 and < 1")
    expected_loss = base * (loss_rate / (Decimal("1") - loss_rate)) if loss_rate else Decimal("0")
    total = base + expected_loss
    return {
        "materials": money(materials_cost),
        "labor": money(labor_cost),
        "fixed": money(fixed_cost),
        "expected_loss": money(expected_loss),
        "unit_cost": money(total),
    }, complete


def preview_order_economics(db: Session, order: Order) -> Economics:
    company = db.get(Company, order.company_id)
    subtotal = sum((D(i.qty) * D(i.unit_price) for i in order.items), Decimal("0"))
    revenue = subtotal - D(order.discount) + D(order.freight_charged)
    total_prod = Decimal("0")
    mats = lab = fixed = loss = Decimal("0")
    complete = True
    for item in order.items:
        c, ok = calculate_product_unit_cost(db, company, item.product)
        complete = complete and ok
        q = D(item.qty)
        total_prod += D(c["unit_cost"]) * q
        mats += D(c["materials"]) * q
        lab += D(c["labor"]) * q
        fixed += D(c["fixed"]) * q
        loss += D(c["expected_loss"]) * q
    if not complete:
        return Economics(money(revenue), None, None, None, "INCOMPLETE")
    fee = revenue * payment_fee_rate(company, order.expected_payment_method)
    est = total_prod + D(order.company_freight_cost) + fee
    result = revenue - est
    margin = (result / revenue) if revenue > 0 else None
    return Economics(
        revenue=money(revenue),
        estimated_cost=money(est),
        result=money(result),
        margin=margin.quantize(Decimal("0.0001")) if margin is not None else None,
        integrity="COMPLETE",
        materials=money(mats), labor=money(lab), fixed=money(fixed), expected_loss=money(loss),
        company_freight=money(D(order.company_freight_cost)), payment_fee=money(fee),
    )


def snapshot_order_costs(db: Session, order: Order):
    company = db.get(Company, order.company_id)
    if order.snapshots:
        return
    for item in order.items:
        c, complete = calculate_product_unit_cost(db, company, item.product)
        if not complete:
            raise DomainError("Custo incompleto. Confirmação bloqueada até todos os insumos terem custo confirmado.")
        q = D(item.qty)
        db.add(CostSnapshot(
            order_id=order.id,
            order_item_id=item.id,
            materials_cost=c["materials"],
            labor_cost=c["labor"],
            fixed_cost=c["fixed"],
            expected_loss_cost=c["expected_loss"],
            unit_cost=c["unit_cost"],
            total_cost=money(D(c["unit_cost"]) * q),
            integrity="COMPLETE",
            confidence="MEASURED",
        ))


def inventory_on_hand(db: Session, material_id: str) -> Decimal:
    val = db.scalar(select(func.coalesce(func.sum(InventoryTransaction.qty_signed), 0)).where(InventoryTransaction.material_id == material_id))
    return qty(D(val))


def reserved_qty(db: Session, material_id: str) -> Decimal:
    val = db.scalar(select(func.coalesce(func.sum(InventoryReservation.qty), 0)).where(
        InventoryReservation.material_id == material_id,
        InventoryReservation.active.is_(True),
    ))
    return qty(D(val))


def inventory_available(db: Session, material_id: str) -> Decimal:
    return qty(inventory_on_hand(db, material_id) - reserved_qty(db, material_id))


def reservation_for(db: Session, production_id: str, material_id: str) -> InventoryReservation | None:
    return db.scalar(select(InventoryReservation).where(
        InventoryReservation.production_id == production_id,
        InventoryReservation.material_id == material_id,
        InventoryReservation.active.is_(True),
    ))


def production_shortage(db: Session, production: ProductionOrder) -> dict[str, Decimal]:
    shortages: dict[str, Decimal] = {}
    for req in production.requirements:
        res = reservation_for(db, production.id, req.material_id)
        rqty = D(res.qty) if res else Decimal("0")
        missing = max(D(req.required_qty) - rqty, Decimal("0"))
        if missing > 0:
            shortages[req.material_id] = qty(missing)
    return shortages


def reserve_for_production(db: Session, production: ProductionOrder):
    for req in production.requirements:
        res = reservation_for(db, production.id, req.material_id)
        if not res:
            res = InventoryReservation(production_id=production.id, material_id=req.material_id, qty=Decimal("0"), active=True)
            db.add(res)
            db.flush()
        need = max(D(req.required_qty) - D(res.qty), Decimal("0"))
        if need <= 0:
            continue
        available = inventory_available(db, req.material_id)
        add = min(need, max(available, Decimal("0")))
        if add > 0:
            res.qty = qty(D(res.qty) + add)
    db.flush()
    production.status = ProductionStatus.READY if not production_shortage(db, production) else ProductionStatus.BLOCKED


def retry_reservations(db: Session, company_id: str):
    prods = db.scalars(
        select(ProductionOrder)
        .join(Order)
        .where(Order.company_id == company_id, ProductionOrder.status.in_([ProductionStatus.BLOCKED, ProductionStatus.PLANNED, ProductionStatus.IN_PROGRESS]))
        .order_by(Order.confirmed_at.asc().nullslast(), Order.created_at.asc())
    ).all()
    for p in prods:
        before = p.status
        reserve_for_production(db, p)
        if before == ProductionStatus.IN_PROGRESS and p.status == ProductionStatus.READY:
            p.status = ProductionStatus.IN_PROGRESS


def create_production_requirements(db: Session, order: Order, production: ProductionOrder):
    totals: dict[str, Decimal] = {}
    mats: dict[str, Material] = {}
    for oi in order.items:
        versions = [x.version for x in oi.product.technical_items]
        maxv = max(versions or [1])
        for ti in oi.product.technical_items:
            if ti.version != maxv:
                continue
            totals[ti.material_id] = totals.get(ti.material_id, Decimal("0")) + D(ti.qty) * D(oi.qty)
            mats[ti.material_id] = ti.material
    for mid, total in totals.items():
        db.add(MaterialRequirement(production_id=production.id, material_id=mid, required_qty=qty(total)))
    db.flush()


def confirm_order(db: Session, order: Order, actor: User, margin_override_reason: str | None = None):
    if order.commercial_status == CommercialStatus.CONFIRMED:
        return order
    if order.commercial_status != CommercialStatus.DRAFT:
        raise DomainError("Only draft orders can be confirmed")
    if not order.items:
        raise DomainError("Pedido sem itens")
    econ = preview_order_economics(db, order)
    if econ.integrity != "COMPLETE":
        raise DomainError("Custo incompleto. Confirmação bloqueada para qualquer papel.")
    company = db.get(Company, order.company_id)
    if econ.margin is not None and econ.margin < D(company.target_margin):
        if actor.role != Role.MANAGER:
            raise DomainError("Margem abaixo da meta. Solicite decisão do Manager.")
        if not (margin_override_reason or "").strip():
            raise DomainError("Margem abaixo da meta. Informe uma justificativa para o override gerencial.")
        order.margin_override_reason = margin_override_reason.strip()
    snapshot_order_costs(db, order)
    now = datetime.now(timezone.utc)
    order.commercial_status = CommercialStatus.CONFIRMED
    order.confirmed_at = now
    if order.original_promised_at is None:
        order.original_promised_at = order.current_promised_at

    prod = ProductionOrder(order_id=order.id, status=ProductionStatus.PLANNED, due_at=order.current_promised_at)
    db.add(prod)
    db.flush()
    create_production_requirements(db, order, prod)
    reserve_for_production(db, prod)

    method = order.fulfillment_method or FulfillmentMethod.PICKUP
    full = Fulfillment(
        order_id=order.id,
        method=method,
        status=FulfillmentStatus.WAITING_PRODUCTION,
        original_promised_at=order.original_promised_at,
        current_promised_at=order.current_promised_at,
    )
    db.add(full)
    audit(db, order.company_id, actor.id, "order.confirmed", "Order", order.id, {"number": order.number})
    outbox(db, order.company_id, "order.confirmed", "Order", order.id, {"number": order.number})
    db.flush()
    return order


def record_payment(db: Session, order: Order, amount: Decimal, method: PaymentMethod, actor: User):
    if D(amount) <= 0:
        raise DomainError("Payment must be positive")
    company = db.get(Company, order.company_id)
    fee = D(amount) * payment_fee_rate(company, method)
    p = Payment(order_id=order.id, amount=money(amount), method=method, fee_amount=money(fee))
    db.add(p)
    db.flush()
    audit(db, order.company_id, actor.id, "payment.created", "Payment", p.id, {"order": order.number, "amount": str(money(amount))})
    outbox(db, order.company_id, "payment.created", "Payment", p.id, {"order_id": order.id})
    return p


def purchase_total(purchase: Purchase) -> Decimal:
    subtotal = sum((D(i.qty) * D(i.unit_price) for i in purchase.items), Decimal("0"))
    return money(subtotal + D(purchase.freight))


def purchase_item_landed_cost(purchase: Purchase, item: PurchaseItem) -> Decimal:
    subtotal = sum((D(i.qty) * D(i.unit_price) for i in purchase.items), Decimal("0"))
    item_gross = D(item.qty) * D(item.unit_price)
    allocated_freight = (D(purchase.freight) * item_gross / subtotal) if subtotal > 0 else Decimal("0")
    return money((item_gross + allocated_freight) / D(item.qty)) if D(item.qty) > 0 else Decimal("0")


def receive_purchase(db: Session, purchase: Purchase, receipt_quantities: dict[str, Decimal], actor: User):
    receipt = GoodsReceipt(purchase_id=purchase.id)
    db.add(receipt)
    db.flush()
    any_qty = False
    for item in purchase.items:
        received_before = db.scalar(select(func.coalesce(func.sum(GoodsReceiptItem.qty), 0)).where(GoodsReceiptItem.purchase_item_id == item.id))
        pending = max(D(item.qty) - D(received_before), Decimal("0"))
        q = min(max(D(receipt_quantities.get(item.id, 0)), Decimal("0")), pending)
        if q <= 0:
            continue
        any_qty = True
        gri = GoodsReceiptItem(receipt_id=receipt.id, purchase_item_id=item.id, qty=qty(q))
        db.add(gri)
        db.add(InventoryTransaction(
            company_id=purchase.company_id,
            material_id=item.material_id,
            tx_type=InventoryTxType.PURCHASE_IN,
            qty_signed=qty(q),
            reason=f"Recebimento compra C{purchase.number:04d}",
            source_type="GoodsReceipt",
            source_id=receipt.id,
        ))
        # latest confirmed landed acquisition cost per consumption unit
        item.material.current_cost = purchase_item_landed_cost(purchase, item)
    if not any_qty:
        raise DomainError("Informe ao menos uma quantidade recebida")
    db.flush()
    retry_reservations(db, purchase.company_id)
    audit(db, purchase.company_id, actor.id, "goods_receipt.created", "GoodsReceipt", receipt.id, {"purchase": purchase.number})
    outbox(db, purchase.company_id, "goods_receipt.created", "GoodsReceipt", receipt.id, {"purchase_id": purchase.id})
    return receipt


def start_production(db: Session, production: ProductionOrder, actor: User):
    if production.status != ProductionStatus.READY:
        raise DomainError("Somente produção Pronta pode ser iniciada")
    production.status = ProductionStatus.IN_PROGRESS
    production.started_at = datetime.now(timezone.utc)
    audit(db, production.order.company_id, actor.id, "production.started", "ProductionOrder", production.id)
    outbox(db, production.order.company_id, "production.started", "ProductionOrder", production.id)


def record_loss(db: Session, production: ProductionOrder, material_id: str, amount: Decimal, reason: str, actor: User):
    if production.status != ProductionStatus.IN_PROGRESS:
        raise DomainError("Registre perda durante produção em andamento")
    amount = qty(D(amount))
    if amount <= 0:
        raise DomainError("Quantidade inválida")
    res = reservation_for(db, production.id, material_id)
    if not res or D(res.qty) < amount:
        raise DomainError("Perda excede material reservado")
    res.qty = qty(D(res.qty) - amount)
    db.add(ProductionException(production_id=production.id, material_id=material_id, kind="LOSS", qty=amount, reason=reason))
    db.add(InventoryTransaction(
        company_id=production.order.company_id, material_id=material_id, tx_type=InventoryTxType.LOSS_OUT,
        qty_signed=-amount, reason=reason or "Perda de produção", source_type="ProductionOrder", source_id=production.id,
    ))
    db.flush()
    # Try to reserve a replacement immediately. Status stays IN_PROGRESS even if shortage emerges.
    reserve_for_production(db, production)
    production.status = ProductionStatus.IN_PROGRESS
    audit(db, production.order.company_id, actor.id, "production.loss_recorded", "ProductionOrder", production.id, {"material_id": material_id, "qty": str(amount)})
    outbox(db, production.order.company_id, "production.loss_recorded", "ProductionOrder", production.id)


def record_extra(db: Session, production: ProductionOrder, material_id: str, amount: Decimal, reason: str, actor: User):
    if production.status != ProductionStatus.IN_PROGRESS:
        raise DomainError("Registre consumo extra durante produção em andamento")
    amount = qty(D(amount))
    if amount <= 0:
        raise DomainError("Quantidade inválida")
    if inventory_available(db, material_id) < amount:
        raise DomainError("Estoque disponível insuficiente para consumo extra")
    db.add(ProductionException(production_id=production.id, material_id=material_id, kind="EXTRA", qty=amount, reason=reason))
    db.add(InventoryTransaction(
        company_id=production.order.company_id, material_id=material_id, tx_type=InventoryTxType.PRODUCTION_OUT,
        qty_signed=-amount, reason=reason or "EXTRA_CONSUMPTION", source_type="ProductionOrder", source_id=production.id,
    ))
    audit(db, production.order.company_id, actor.id, "production.extra_consumption", "ProductionOrder", production.id, {"material_id": material_id, "qty": str(amount)})


def _ready_status(method: FulfillmentMethod) -> FulfillmentStatus:
    return {
        FulfillmentMethod.PICKUP: FulfillmentStatus.READY_FOR_PICKUP,
        FulfillmentMethod.LOCAL_DELIVERY: FulfillmentStatus.READY_FOR_DELIVERY,
        FulfillmentMethod.SHIPPING: FulfillmentStatus.READY_TO_SHIP,
    }.get(method, FulfillmentStatus.READY_FOR_DELIVERY)


def complete_production(db: Session, production: ProductionOrder, actor: User):
    if production.status != ProductionStatus.IN_PROGRESS:
        raise DomainError("Somente produção Em andamento pode ser concluída")
    shortage = production_shortage(db, production)
    if shortage:
        raise DomainError("Não é possível concluir enquanto houver falta de material reservado")
    for req in production.requirements:
        res = reservation_for(db, production.id, req.material_id)
        amount = D(req.required_qty)
        if not res or D(res.qty) < amount:
            raise DomainError("Reserva inconsistente para conclusão")
        db.add(InventoryTransaction(
            company_id=production.order.company_id, material_id=req.material_id, tx_type=InventoryTxType.PRODUCTION_OUT,
            qty_signed=-qty(amount), reason="Consumo normal na conclusão", source_type="ProductionOrder", source_id=production.id,
        ))
        res.qty = Decimal("0")
        res.active = False
    production.status = ProductionStatus.COMPLETED
    production.completed_at = datetime.now(timezone.utc)
    fulfillment = production.order.fulfillment
    if fulfillment:
        fulfillment.status = _ready_status(fulfillment.method)
        fulfillment.ready_at = datetime.now(timezone.utc)
    audit(db, production.order.company_id, actor.id, "production.completed", "ProductionOrder", production.id)
    outbox(db, production.order.company_id, "production.completed", "ProductionOrder", production.id)


def start_delivery(db: Session, fulfillment: Fulfillment, actor: User):
    if fulfillment.method != FulfillmentMethod.LOCAL_DELIVERY:
        raise DomainError("Este fluxo é apenas para entrega local")
    if fulfillment.status != FulfillmentStatus.READY_FOR_DELIVERY:
        raise DomainError("Entrega não está pronta para iniciar")
    fulfillment.status = FulfillmentStatus.OUT_FOR_DELIVERY
    attempt = DeliveryAttempt(fulfillment_id=fulfillment.id, status="STARTED")
    db.add(attempt)
    audit(db, fulfillment.order.company_id, actor.id, "fulfillment.delivery_started", "Fulfillment", fulfillment.id)
    return attempt


def fail_delivery(db: Session, fulfillment: Fulfillment, reason: str, actor: User):
    if fulfillment.status != FulfillmentStatus.OUT_FOR_DELIVERY:
        raise DomainError("Não existe entrega em andamento")
    attempt = next((a for a in reversed(fulfillment.attempts) if a.status == "STARTED"), None)
    if not attempt:
        attempt = DeliveryAttempt(fulfillment_id=fulfillment.id, status="FAILED")
        db.add(attempt)
    attempt.status = "FAILED"
    attempt.reason = reason
    attempt.ended_at = datetime.now(timezone.utc)
    fulfillment.status = FulfillmentStatus.READY_FOR_DELIVERY
    audit(db, fulfillment.order.company_id, actor.id, "fulfillment.delivery_failed", "Fulfillment", fulfillment.id, {"reason": reason})


def complete_fulfillment(db: Session, fulfillment: Fulfillment, actor: User):
    now = datetime.now(timezone.utc)
    if fulfillment.method == FulfillmentMethod.PICKUP:
        if fulfillment.status != FulfillmentStatus.READY_FOR_PICKUP:
            raise DomainError("Pedido não está pronto para retirada")
        fulfillment.status = FulfillmentStatus.PICKED_UP
    elif fulfillment.method == FulfillmentMethod.LOCAL_DELIVERY:
        if fulfillment.status != FulfillmentStatus.OUT_FOR_DELIVERY:
            raise DomainError("Entrega não está em andamento")
        attempt = next((a for a in reversed(fulfillment.attempts) if a.status == "STARTED"), None)
        if attempt:
            attempt.status = "DELIVERED"
            attempt.ended_at = now
        fulfillment.status = FulfillmentStatus.DELIVERED
    elif fulfillment.method == FulfillmentMethod.SHIPPING:
        if fulfillment.status != FulfillmentStatus.READY_TO_SHIP:
            raise DomainError("Pedido não está pronto para postagem")
        fulfillment.status = FulfillmentStatus.POSTED
    else:
        fulfillment.status = FulfillmentStatus.DELIVERED
    fulfillment.completed_at = now
    audit(db, fulfillment.order.company_id, actor.id, "fulfillment.completed", "Fulfillment", fulfillment.id, {"status": fulfillment.status.value})
    outbox(db, fulfillment.order.company_id, "fulfillment.completed", "Fulfillment", fulfillment.id)


def order_payment_summary(order: Order) -> tuple[Decimal, Decimal, str]:
    paid = sum((D(p.amount) for p in order.payments if not p.reversed), Decimal("0"))
    subtotal = sum((D(i.qty) * D(i.unit_price) for i in order.items), Decimal("0"))
    total = subtotal - D(order.discount) + D(order.freight_charged)
    balance = max(total - paid, Decimal("0"))
    status = "PAID" if balance <= 0 and total > 0 else ("PARTIAL" if paid > 0 else "UNPAID")
    return money(paid), money(balance), status


def aggregated_dashboard(db: Session, company_id: str) -> dict:
    orders = db.scalars(select(Order).where(Order.company_id == company_id, Order.commercial_status == CommercialStatus.CONFIRMED)).all()
    revenue = Decimal("0")
    cost = Decimal("0")
    received = Decimal("0")
    profitable = None
    profitable_result = None
    for o in orders:
        subtotal = sum((D(i.qty) * D(i.unit_price) for i in o.items), Decimal("0"))
        rev = subtotal - D(o.discount) + D(o.freight_charged)
        snap_cost = sum((D(s.total_cost) for s in o.snapshots), Decimal("0"))
        fee_est = rev * payment_fee_rate(db.get(Company, company_id), o.expected_payment_method)
        ocost = snap_cost + D(o.company_freight_cost) + fee_est
        res = rev - ocost
        revenue += rev
        cost += ocost
        received += sum((D(p.amount) for p in o.payments if not p.reversed), Decimal("0"))
        if profitable_result is None or res > profitable_result:
            profitable_result = res
            profitable = o
    result = revenue - cost
    margin = result / revenue if revenue > 0 else None
    late = risk = today = ready = 0
    now = datetime.now(timezone.utc)
    company = db.get(Company, company_id)
    for o in orders:
        p = o.production
        f = o.fulfillment
        if f and f.status in [FulfillmentStatus.PICKED_UP, FulfillmentStatus.DELIVERED, FulfillmentStatus.POSTED, FulfillmentStatus.CANCELLED]:
            continue
        promised = o.current_promised_at
        if promised and now > promised:
            late += 1
        elif promised and p and p.status != ProductionStatus.COMPLETED and now >= promised - timedelta(hours=company.risk_window_hours):
            risk += 1
        elif promised and promised.date() == now.date():
            today += 1
        elif f and f.status in [FulfillmentStatus.READY_FOR_PICKUP, FulfillmentStatus.READY_FOR_DELIVERY, FulfillmentStatus.READY_TO_SHIP]:
            ready += 1
    return {
        "revenue": money(revenue), "cost": money(cost), "result": money(result),
        "margin": margin.quantize(Decimal("0.0001")) if margin is not None else None,
        "received": money(received), "receivable": money(max(revenue - received, Decimal("0"))),
        "profitable_order": profitable, "profitable_result": money(profitable_result or 0),
        "late": late, "risk": risk, "today": today, "ready": ready,
    }


def change_promise(db: Session, order: Order, new_promised_at: datetime, reason: str, notes: str, actor: User):
    from .models import PromiseChange
    previous = order.current_promised_at
    db.add(PromiseChange(
        order_id=order.id,
        previous_promised_at=previous,
        new_promised_at=new_promised_at,
        reason=reason,
        notes=notes or None,
        changed_by_user_id=actor.id,
    ))
    order.current_promised_at = new_promised_at
    if order.fulfillment:
        order.fulfillment.current_promised_at = new_promised_at
    if order.production:
        order.production.due_at = new_promised_at
    audit(db, order.company_id, actor.id, "fulfillment.promise_changed", "Order", order.id, {"previous": str(previous), "new": str(new_promised_at), "reason": reason})
    outbox(db, order.company_id, "fulfillment.promise_changed", "Order", order.id)


def reverse_payment(db: Session, payment: Payment, actor: User, reason: str):
    if payment.reversed:
        return payment
    payment.reversed = True
    audit(db, payment.order.company_id, actor.id, "payment.reversed", "Payment", payment.id, {"reason": reason})
    outbox(db, payment.order.company_id, "payment.reversed", "Payment", payment.id)
    return payment


def cancel_order(db: Session, order: Order, actor: User, reason: str):
    if order.commercial_status == CommercialStatus.CANCELLED:
        return order
    if order.commercial_status == CommercialStatus.DRAFT:
        order.commercial_status = CommercialStatus.CANCELLED
        order.cancelled_at = datetime.now(timezone.utc)
        audit(db, order.company_id, actor.id, "order.cancelled", "Order", order.id, {"reason": reason})
        return order
    # Release any unconsumed reservation; physical ledger movements remain immutable.
    if order.production:
        for res in order.production.reservations:
            if res.active:
                res.qty = Decimal("0")
                res.active = False
        if order.production.status != ProductionStatus.COMPLETED:
            order.production.status = ProductionStatus.CANCELLED
    if order.fulfillment:
        order.fulfillment.status = FulfillmentStatus.CANCELLED
    order.commercial_status = CommercialStatus.CANCELLED
    order.cancelled_at = datetime.now(timezone.utc)
    audit(db, order.company_id, actor.id, "order.cancelled", "Order", order.id, {"reason": reason})
    outbox(db, order.company_id, "order.cancelled", "Order", order.id)
    return order


def reconcile(db: Session, company_id: str) -> list[dict]:
    issues: list[dict] = []
    orders = db.scalars(select(Order).where(Order.company_id == company_id)).all()
    for o in orders:
        if o.commercial_status == CommercialStatus.CONFIRMED:
            if not o.snapshots:
                issues.append({"severity":"critical","entity":f"Order #{o.number}","message":"Pedido confirmado sem CostSnapshot"})
            if not o.production:
                issues.append({"severity":"critical","entity":f"Order #{o.number}","message":"Pedido confirmado sem ProductionOrder"})
            if not o.fulfillment:
                issues.append({"severity":"critical","entity":f"Order #{o.number}","message":"Pedido confirmado sem Fulfillment"})
        if o.production and o.production.status == ProductionStatus.COMPLETED:
            active = [r for r in o.production.reservations if r.active and D(r.qty) > 0]
            if active:
                issues.append({"severity":"critical","entity":f"Production {o.production.id[:8]}","message":"Produção concluída com reserva ativa"})
    materials = db.scalars(select(Material).where(Material.company_id == company_id)).all()
    for m in materials:
        on_hand = inventory_on_hand(db, m.id)
        reserved = reserved_qty(db, m.id)
        if reserved > on_hand:
            issues.append({"severity":"warning","entity":m.name,"message":f"Reservado {reserved} > On Hand {on_hand}"})
    return issues
