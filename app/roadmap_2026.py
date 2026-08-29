from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timezone
from decimal import Decimal

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .db import get_db
from .domain import D, inventory_available, inventory_on_hand, order_payment_summary, reserved_qty
from .models import (
    CommercialStatus,
    Customer,
    FulfillmentMethod,
    InventoryTransaction,
    Material,
    Order,
    OrderItem,
    PaymentMethod,
    Product,
    TechnicalSheetItem,
    User,
)
from .real_costs import REAL_PRODUCT_COST_REFERENCES
from .roadmap_models import (
    CatalogCategory,
    MaterialCategoryAssignment,
    ProductCategoryAssignment,
)

DEFAULT_CATEGORIES = ("Canecas", "Camisetas", "Sublimação", "Embalagens")


def _remove_route(app, path: str, method: str) -> None:
    for route in list(app.router.routes):
        if getattr(route, "path", None) == path and method.upper() in (getattr(route, "methods", None) or set()):
            app.router.routes.remove(route)


def _clean(value) -> str:
    return " ".join(str(value or "").strip().split())


def _category_options(db: Session, company_id: str) -> list[str]:
    names = {
        name
        for name in db.scalars(
            select(CatalogCategory.name)
            .where(CatalogCategory.company_id == company_id, CatalogCategory.active.is_(True))
            .order_by(CatalogCategory.name)
        ).all()
    }
    names.update(DEFAULT_CATEGORIES)
    return sorted(names, key=str.casefold)


def _get_or_create_category(db: Session, company_id: str, raw_name: str) -> CatalogCategory | None:
    name = _clean(raw_name)
    if not name:
        return None
    category = db.scalar(
        select(CatalogCategory).where(
            CatalogCategory.company_id == company_id,
            func.lower(CatalogCategory.name) == name.lower(),
        )
    )
    if category:
        if not category.active:
            category.active = True
        return category
    category = CatalogCategory(company_id=company_id, name=name)
    db.add(category)
    db.flush()
    return category


def _set_material_category(db: Session, material: Material, raw_name: str) -> None:
    existing = db.get(MaterialCategoryAssignment, material.id)
    category = _get_or_create_category(db, material.company_id, raw_name)
    if category is None:
        if existing:
            db.delete(existing)
        return
    if existing:
        existing.category_id = category.id
    else:
        db.add(MaterialCategoryAssignment(material_id=material.id, category_id=category.id))


def _set_product_category(db: Session, product: Product, raw_name: str) -> None:
    existing = db.get(ProductCategoryAssignment, product.id)
    category = _get_or_create_category(db, product.company_id, raw_name)
    if category is None:
        if existing:
            db.delete(existing)
        return
    if existing:
        existing.category_id = category.id
    else:
        db.add(ProductCategoryAssignment(product_id=product.id, category_id=category.id))


def _material_category_map(db: Session, company_id: str) -> dict[str, str]:
    rows = db.execute(
        select(MaterialCategoryAssignment.material_id, CatalogCategory.name)
        .join(CatalogCategory, CatalogCategory.id == MaterialCategoryAssignment.category_id)
        .join(Material, Material.id == MaterialCategoryAssignment.material_id)
        .where(Material.company_id == company_id)
    ).all()
    return {material_id: name for material_id, name in rows}


def _product_category_map(db: Session, company_id: str) -> dict[str, str]:
    rows = db.execute(
        select(ProductCategoryAssignment.product_id, CatalogCategory.name)
        .join(CatalogCategory, CatalogCategory.id == ProductCategoryAssignment.category_id)
        .join(Product, Product.id == ProductCategoryAssignment.product_id)
        .where(Product.company_id == company_id)
    ).all()
    return {product_id: name for product_id, name in rows}


def _parse_date(value: str, *, end: bool = False) -> datetime | None:
    value = _clean(value)
    if not value:
        return None
    try:
        day = datetime.fromisoformat(value).date()
    except ValueError:
        return None
    return datetime.combine(day, time.max if end else time.min, tzinfo=timezone.utc)


def _enum_or_none(enum_cls, value: str):
    value = _clean(value)
    if not value:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None


def _technical_rows_from_form(form) -> tuple[list[tuple[str, Decimal]], str | None]:
    material_ids = [str(x) for x in form.getlist("material_id")]
    quantities = [D(x or 0) for x in form.getlist("quantity")]
    rows: list[tuple[str, Decimal]] = []
    seen: set[str] = set()
    for material_id, qty in zip(material_ids, quantities):
        if not material_id or qty <= 0:
            continue
        if material_id in seen:
            return [], "Não repita o mesmo insumo na ficha técnica."
        seen.add(material_id)
        rows.append((material_id, qty))
    return rows, None


def install_roadmap_2026(main_module) -> None:
    app = main_module.app
    if getattr(app.state, "roadmap_2026_phase1_installed", False):
        return

    for path, method in [
        ("/orders", "GET"),
        ("/inventory", "GET"),
        ("/catalog", "GET"),
        ("/products", "POST"),
        ("/materials/{material_id}/cost", "POST"),
    ]:
        _remove_route(app, path, method)

    @app.get("/orders", response_class=HTMLResponse)
    def orders_2026(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_user),
    ):
        params = request.query_params
        search_text = _clean(params.get("q"))
        customer_id = _clean(params.get("customer_id"))
        status = _enum_or_none(CommercialStatus, params.get("status", ""))
        payment_method = _enum_or_none(PaymentMethod, params.get("payment_method", ""))
        fulfillment_method = _enum_or_none(FulfillmentMethod, params.get("fulfillment_method", ""))
        date_from = _parse_date(params.get("date_from", ""))
        date_to = _parse_date(params.get("date_to", ""), end=True)

        stmt = (
            select(Order)
            .join(Customer, Customer.id == Order.customer_id)
            .options(
                selectinload(Order.customer),
                selectinload(Order.production),
                selectinload(Order.payments),
            )
            .where(Order.company_id == user.company_id)
        )
        if search_text:
            pattern = f"%{search_text}%"
            stmt = stmt.where(
                or_(
                    cast(Order.number, String).ilike(pattern),
                    Customer.name.ilike(pattern),
                    Customer.phone.ilike(pattern),
                )
            )
        if customer_id:
            stmt = stmt.where(Order.customer_id == customer_id)
        if status:
            stmt = stmt.where(Order.commercial_status == status)
        if payment_method:
            stmt = stmt.where(Order.expected_payment_method == payment_method)
        if fulfillment_method:
            stmt = stmt.where(Order.fulfillment_method == fulfillment_method)
        if date_from:
            stmt = stmt.where(Order.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Order.created_at <= date_to)

        rows = db.scalars(stmt.order_by(Order.created_at.desc())).unique().all()
        customers = db.scalars(
            select(Customer).where(Customer.company_id == user.company_id).order_by(Customer.name)
        ).all()
        return main_module.render(
            request,
            "orders.html",
            user=user,
            orders=rows,
            customers=customers,
            statuses=list(CommercialStatus),
            payment_methods=list(PaymentMethod),
            fulfillment_methods=list(FulfillmentMethod),
            filters={
                "q": search_text,
                "customer_id": customer_id,
                "status": status.value if status else "",
                "payment_method": payment_method.value if payment_method else "",
                "fulfillment_method": fulfillment_method.value if fulfillment_method else "",
                "date_from": params.get("date_from", ""),
                "date_to": params.get("date_to", ""),
            },
            payment_summary=order_payment_summary,
        )

    @app.get("/inventory", response_class=HTMLResponse)
    def inventory_2026(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_user),
    ):
        category_filter = _clean(request.query_params.get("category"))
        low_only = request.query_params.get("low_stock") in {"1", "true", "on", "yes"}
        mats = db.scalars(
            select(Material).where(Material.company_id == user.company_id).order_by(Material.name)
        ).all()
        category_map = _material_category_map(db, user.company_id)
        rows = []
        for material in mats:
            on_hand = inventory_on_hand(db, material.id)
            reserved = reserved_qty(db, material.id)
            available = inventory_available(db, material.id)
            category = category_map.get(material.id, "Sem categoria")
            is_low = available < D(material.min_stock)
            row = {
                "material": material,
                "on_hand": on_hand,
                "reserved": reserved,
                "available": available,
                "category": category,
                "is_low": is_low,
            }
            if category_filter and category != category_filter:
                continue
            if low_only and not is_low:
                continue
            rows.append(row)

        txs = db.scalars(
            select(InventoryTransaction)
            .where(InventoryTransaction.company_id == user.company_id)
            .order_by(InventoryTransaction.created_at.desc())
            .limit(50)
        ).all()
        return main_module.render(
            request,
            "inventory.html",
            user=user,
            rows=rows,
            txs=txs,
            categories=_category_options(db, user.company_id),
            filters={"category": category_filter, "low_stock": low_only},
        )

    @app.get("/catalog", response_class=HTMLResponse)
    def catalog_2026(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_user),
    ):
        products = db.scalars(
            select(Product)
            .where(Product.company_id == user.company_id)
            .options(selectinload(Product.technical_items).selectinload(TechnicalSheetItem.material))
            .order_by(Product.name)
        ).all()
        materials = db.scalars(
            select(Material).where(Material.company_id == user.company_id).order_by(Material.name)
        ).all()
        product_categories = _product_category_map(db, user.company_id)
        material_categories = _material_category_map(db, user.company_id)

        product_groups: dict[str, list[Product]] = defaultdict(list)
        for product in products:
            product_groups[product_categories.get(product.id, "Sem categoria")].append(product)
        material_groups: dict[str, list[Material]] = defaultdict(list)
        for material in materials:
            material_groups[material_categories.get(material.id, "Sem categoria")].append(material)

        return main_module.render(
            request,
            "catalog.html",
            user=user,
            products=products,
            materials=materials,
            product_categories=product_categories,
            material_categories=material_categories,
            product_groups=dict(sorted(product_groups.items())),
            material_groups=dict(sorted(material_groups.items())),
            reference_costs=REAL_PRODUCT_COST_REFERENCES,
        )

    @app.get("/materials/new", response_class=HTMLResponse)
    def material_new(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        return main_module.render(
            request,
            "material_form_2026.html",
            user=user,
            material=None,
            current_category="",
            categories=_category_options(db, user.company_id),
        )

    @app.post("/materials/new")
    async def material_create(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        form = await request.form()
        name = _clean(form.get("name"))
        unit = _clean(form.get("unit")) or "un"
        if not name:
            main_module.flash(request, "Nome do material é obrigatório.", "danger")
            return RedirectResponse("/materials/new", 303)
        material = Material(
            company_id=user.company_id,
            name=name,
            unit=unit,
            current_cost=D(form.get("current_cost")) if _clean(form.get("current_cost")) else None,
            min_stock=D(form.get("min_stock") or 0),
            active=str(form.get("active") or "1") == "1",
        )
        db.add(material)
        db.flush()
        _set_material_category(db, material, str(form.get("category") or ""))
        db.commit()
        main_module.flash(request, "Material cadastrado.", "success")
        return RedirectResponse("/catalog", 303)

    @app.get("/materials/{material_id}/edit", response_class=HTMLResponse)
    def material_edit(
        request: Request,
        material_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        material = db.get(Material, material_id)
        if not material or material.company_id != user.company_id:
            raise HTTPException(404)
        return main_module.render(
            request,
            "material_form_2026.html",
            user=user,
            material=material,
            current_category=_material_category_map(db, user.company_id).get(material.id, ""),
            categories=_category_options(db, user.company_id),
        )

    @app.post("/materials/{material_id}/edit")
    async def material_update(
        request: Request,
        material_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        material = db.get(Material, material_id)
        if not material or material.company_id != user.company_id:
            raise HTTPException(404)
        form = await request.form()
        name = _clean(form.get("name"))
        if not name:
            main_module.flash(request, "Nome do material é obrigatório.", "danger")
            return RedirectResponse(f"/materials/{material_id}/edit", 303)
        material.name = name
        material.unit = _clean(form.get("unit")) or "un"
        material.current_cost = D(form.get("current_cost")) if _clean(form.get("current_cost")) else None
        material.min_stock = D(form.get("min_stock") or 0)
        material.active = str(form.get("active") or "1") == "1"
        _set_material_category(db, material, str(form.get("category") or ""))
        db.commit()
        main_module.flash(request, "Material atualizado.", "success")
        return RedirectResponse("/catalog", 303)

    @app.post("/materials/{material_id}/cost")
    async def material_cost_2026(
        request: Request,
        material_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        material = db.get(Material, material_id)
        if not material or material.company_id != user.company_id:
            raise HTTPException(404)
        form = await request.form()
        material.current_cost = D(form.get("cost") or 0)
        material.min_stock = D(form.get("min_stock") or 0)
        if "category" in form:
            _set_material_category(db, material, str(form.get("category") or ""))
        db.commit()
        main_module.flash(request, "Custo e estoque mínimo atualizados para vendas futuras.", "success")
        return RedirectResponse("/catalog", 303)

    @app.get("/products/new", response_class=HTMLResponse)
    def product_new(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        materials = db.scalars(
            select(Material).where(Material.company_id == user.company_id, Material.active.is_(True)).order_by(Material.name)
        ).all()
        return main_module.render(
            request,
            "product_form_2026.html",
            user=user,
            product=None,
            technical_items=[],
            materials=materials,
            current_category="",
            categories=_category_options(db, user.company_id),
        )

    @app.post("/products")
    async def product_create_2026(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        form = await request.form()
        name = _clean(form.get("name"))
        if not name:
            main_module.flash(request, "Nome do produto é obrigatório.", "danger")
            return RedirectResponse("/products/new", 303)
        technical_rows, error = _technical_rows_from_form(form)
        if error:
            main_module.flash(request, error, "danger")
            return RedirectResponse("/products/new", 303)

        product = Product(
            company_id=user.company_id,
            name=name,
            base_price=D(form.get("base_price") or 0),
            labor_minutes=int(form.get("labor_minutes") or 0),
            expected_loss_rate=D(form.get("expected_loss_rate") or 0) / 100,
            standard_lead_time_days=int(form.get("standard_lead_time_days") or 3),
            active=str(form.get("active") or "1") == "1",
        )
        db.add(product)
        db.flush()
        _set_product_category(db, product, str(form.get("category") or ""))
        for material_id, qty in technical_rows:
            material = db.get(Material, material_id)
            if material and material.company_id == user.company_id:
                db.add(
                    TechnicalSheetItem(
                        product_id=product.id,
                        material_id=material.id,
                        qty=qty,
                        version=1,
                    )
                )
        db.commit()
        main_module.flash(request, "Produto e ficha técnica criados.", "success")
        return RedirectResponse("/catalog", 303)

    @app.get("/products/{product_id}/edit", response_class=HTMLResponse)
    def product_edit(
        request: Request,
        product_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        product = db.scalar(
            select(Product)
            .options(selectinload(Product.technical_items).selectinload(TechnicalSheetItem.material))
            .where(Product.id == product_id, Product.company_id == user.company_id)
        )
        if not product:
            raise HTTPException(404)
        materials = db.scalars(
            select(Material).where(Material.company_id == user.company_id, Material.active.is_(True)).order_by(Material.name)
        ).all()
        return main_module.render(
            request,
            "product_form_2026.html",
            user=user,
            product=product,
            technical_items=sorted(product.technical_items, key=lambda row: row.material.name.casefold()),
            materials=materials,
            current_category=_product_category_map(db, user.company_id).get(product.id, ""),
            categories=_category_options(db, user.company_id),
        )

    @app.post("/products/{product_id}/edit")
    async def product_update(
        request: Request,
        product_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        product = db.scalar(
            select(Product)
            .options(selectinload(Product.technical_items))
            .where(Product.id == product_id, Product.company_id == user.company_id)
        )
        if not product:
            raise HTTPException(404)
        form = await request.form()
        name = _clean(form.get("name"))
        if not name:
            main_module.flash(request, "Nome do produto é obrigatório.", "danger")
            return RedirectResponse(f"/products/{product_id}/edit", 303)
        technical_rows, error = _technical_rows_from_form(form)
        if error:
            main_module.flash(request, error, "danger")
            return RedirectResponse(f"/products/{product_id}/edit", 303)

        product.name = name
        product.base_price = D(form.get("base_price") or 0)
        product.labor_minutes = int(form.get("labor_minutes") or 0)
        product.expected_loss_rate = D(form.get("expected_loss_rate") or 0) / 100
        product.standard_lead_time_days = int(form.get("standard_lead_time_days") or 3)
        product.active = str(form.get("active") or "1") == "1"
        _set_product_category(db, product, str(form.get("category") or ""))

        previous_versions = [row.version for row in product.technical_items] or [0]
        next_version = max(previous_versions) + 1
        for row in list(product.technical_items):
            db.delete(row)
        db.flush()
        for material_id, qty in technical_rows:
            material = db.get(Material, material_id)
            if material and material.company_id == user.company_id:
                db.add(
                    TechnicalSheetItem(
                        product_id=product.id,
                        material_id=material.id,
                        qty=qty,
                        version=next_version,
                    )
                )
        db.commit()
        main_module.flash(
            request,
            "Produto atualizado. Pedidos confirmados mantêm os snapshots e requisitos já registrados.",
            "success",
        )
        return RedirectResponse("/catalog", 303)

    app.state.roadmap_2026_phase1_installed = True
