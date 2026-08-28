from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .db import get_db
from .domain import D, next_order_number
from .enhancement_models import CustomerAddress, SupplierMaterial, SupplierProfile
from .models import (
    Customer,
    FulfillmentMethod,
    Material,
    Order,
    OrderItem,
    PaymentMethod,
    Product,
    Role,
    Supplier,
    User,
)


def _digits(value: str | None, label: str = "Telefone") -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise ValueError(f"{label} deve conter somente números.")
    return value


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _remove_route(app, path: str, method: str) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method.upper() in (getattr(route, "methods", None) or set())
        )
    ]


def _upsert_customer_address(db: Session, customer_id: str, form) -> CustomerAddress | None:
    fields = {
        "address_line": _clean(form.get("address_line")),
        "number": _clean(form.get("address_number")),
        "complement": _clean(form.get("address_complement")),
        "neighborhood": _clean(form.get("neighborhood")),
        "city": _clean(form.get("city")),
        "state": _clean(form.get("state")),
        "postal_code": _clean(form.get("postal_code")),
    }
    profile = db.scalar(select(CustomerAddress).where(CustomerAddress.customer_id == customer_id))
    if not profile and any(fields.values()):
        profile = CustomerAddress(customer_id=customer_id, **fields)
        db.add(profile)
    elif profile:
        for key, value in fields.items():
            setattr(profile, key, value)
    return profile


def _valid_material_ids(db: Session, company_id: str, ids: list[str]) -> list[str]:
    if not ids:
        return []
    rows = db.scalars(select(Material.id).where(Material.company_id == company_id, Material.id.in_(ids))).all()
    return list(rows)


def _replace_supplier_materials(db: Session, supplier_id: str, company_id: str, ids: list[str]) -> None:
    for row in db.scalars(select(SupplierMaterial).where(SupplierMaterial.supplier_id == supplier_id)).all():
        db.delete(row)
    for material_id in _valid_material_ids(db, company_id, ids):
        db.add(SupplierMaterial(supplier_id=supplier_id, material_id=material_id))


def register_enhancements(app) -> None:
    if getattr(app.state, "criativas_enhancements_registered", False):
        return
    app.state.criativas_enhancements_registered = True

    from .main import flash, render, require_manager, require_user

    for path, method in [
        ("/orders/new", "GET"),
        ("/orders/new", "POST"),
        ("/people", "GET"),
        ("/customers", "POST"),
        ("/suppliers", "POST"),
    ]:
        _remove_route(app, path, method)

    @app.get("/orders/new", response_class=HTMLResponse)
    def enhanced_order_new(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(require_user),
    ):
        customers = db.scalars(
            select(Customer).where(Customer.company_id == user.company_id).order_by(Customer.name)
        ).all()
        products = db.scalars(
            select(Product).where(Product.company_id == user.company_id, Product.active.is_(True)).order_by(Product.name)
        ).all()
        return render(
            request,
            "order_form.html",
            user=user,
            customers=customers,
            products=products,
            payment_methods=list(PaymentMethod),
            methods=list(FulfillmentMethod),
        )

    @app.post("/orders/new")
    async def enhanced_order_create(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(require_user),
    ):
        form = await request.form()
        customer_id = str(form.get("customer_id") or "").strip()
        customer = None

        if customer_id and customer_id != "__new__":
            customer = db.get(Customer, customer_id)
            if not customer or customer.company_id != user.company_id:
                raise HTTPException(400)
        else:
            name = str(form.get("new_customer_name") or "").strip()
            email = _clean(form.get("new_customer_email"))
            try:
                phone = _digits(form.get("new_customer_phone"))
            except ValueError as exc:
                flash(request, str(exc), "danger")
                return RedirectResponse("/orders/new", status_code=303)
            if not name:
                flash(request, "Informe o nome do novo cliente ou selecione um cliente cadastrado.", "danger")
                return RedirectResponse("/orders/new", status_code=303)

            if phone:
                customer = db.scalar(
                    select(Customer).where(Customer.company_id == user.company_id, Customer.phone == phone)
                )
            if not customer and email:
                customer = db.scalar(
                    select(Customer).where(
                        Customer.company_id == user.company_id,
                        func.lower(Customer.email) == email.lower(),
                    )
                )
            if not customer:
                customer = Customer(
                    company_id=user.company_id,
                    name=name,
                    phone=phone,
                    email=email,
                )
                db.add(customer)
                db.flush()
            _upsert_customer_address(db, customer.id, form)

        product_ids = [str(x) for x in form.getlist("product_id")]
        quantities = [D(x or 0) for x in form.getlist("quantity")]
        unit_prices = [D(x or 0) for x in form.getlist("unit_price")]
        discount = D(form.get("discount") or 0)
        freight_charged = D(form.get("freight_charged") or 0)
        company_freight_cost = D(form.get("company_freight_cost") or 0)
        expected_payment_method = PaymentMethod(str(form.get("expected_payment_method") or PaymentMethod.PIX.value))
        fulfillment_method = FulfillmentMethod(str(form.get("fulfillment_method") or FulfillmentMethod.PICKUP.value))
        promised_at = str(form.get("promised_at") or "")
        priority = str(form.get("priority") or "NORMAL")
        dt = datetime.fromisoformat(promised_at).replace(tzinfo=timezone.utc) if promised_at else None

        order = Order(
            company_id=user.company_id,
            number=next_order_number(db),
            customer_id=customer.id,
            discount=discount,
            freight_charged=freight_charged,
            company_freight_cost=company_freight_cost,
            expected_payment_method=expected_payment_method,
            fulfillment_method=fulfillment_method,
            original_promised_at=dt,
            current_promised_at=dt,
            priority=priority,
        )
        db.add(order)
        db.flush()

        count = 0
        for pid, qty, price in zip(product_ids, quantities, unit_prices):
            if qty <= 0:
                continue
            product = db.get(Product, pid)
            if not product or product.company_id != user.company_id:
                continue
            db.add(OrderItem(order_id=order.id, product_id=pid, qty=qty, unit_price=price))
            count += 1
        if count == 0:
            db.rollback()
            flash(request, "Adicione ao menos um item válido.", "danger")
            return RedirectResponse("/orders/new", status_code=303)

        db.commit()
        flash(request, f"Pedido #{order.number} criado. Cliente salvo automaticamente quando novo.", "success")
        return RedirectResponse(f"/orders/{order.id}", status_code=303)

    @app.get("/people", response_class=HTMLResponse)
    def enhanced_people(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(require_user),
    ):
        customers = db.scalars(
            select(Customer).where(Customer.company_id == user.company_id).order_by(Customer.name)
        ).all()
        suppliers = db.scalars(
            select(Supplier).where(Supplier.company_id == user.company_id).order_by(Supplier.name)
        ).all()
        materials = db.scalars(
            select(Material).where(Material.company_id == user.company_id, Material.active.is_(True)).order_by(Material.name)
        ).all()

        customer_ids = [c.id for c in customers]
        supplier_ids = [s.id for s in suppliers]
        addresses = {
            row.customer_id: row
            for row in (
                db.scalars(select(CustomerAddress).where(CustomerAddress.customer_id.in_(customer_ids))).all()
                if customer_ids
                else []
            )
        }
        supplier_profiles = {
            row.supplier_id: row
            for row in (
                db.scalars(select(SupplierProfile).where(SupplierProfile.supplier_id.in_(supplier_ids))).all()
                if supplier_ids
                else []
            )
        }
        links_by_supplier: dict[str, list[SupplierMaterial]] = {sid: [] for sid in supplier_ids}
        if supplier_ids:
            links = db.scalars(
                select(SupplierMaterial)
                .options(selectinload(SupplierMaterial.material))
                .where(SupplierMaterial.supplier_id.in_(supplier_ids))
            ).all()
            for link in links:
                links_by_supplier.setdefault(link.supplier_id, []).append(link)

        return render(
            request,
            "people.html",
            user=user,
            customers=customers,
            suppliers=suppliers,
            materials=materials,
            addresses=addresses,
            supplier_profiles=supplier_profiles,
            supplier_links=links_by_supplier,
        )

    @app.post("/customers")
    async def enhanced_customer_create(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(require_user),
    ):
        form = await request.form()
        name = str(form.get("name") or "").strip()
        if not name:
            flash(request, "Nome do cliente é obrigatório.", "danger")
            return RedirectResponse("/people", 303)
        try:
            phone = _digits(form.get("phone"))
        except ValueError as exc:
            flash(request, str(exc), "danger")
            return RedirectResponse("/people", 303)
        customer = Customer(
            company_id=user.company_id,
            name=name,
            phone=phone,
            email=_clean(form.get("email")),
        )
        db.add(customer)
        db.flush()
        _upsert_customer_address(db, customer.id, form)
        db.commit()
        flash(request, "Cliente cadastrado.", "success")
        return RedirectResponse("/people", 303)

    @app.post("/customers/{customer_id}")
    async def enhanced_customer_update(
        request: Request,
        customer_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(require_user),
    ):
        customer = db.get(Customer, customer_id)
        if not customer or customer.company_id != user.company_id:
            raise HTTPException(404)
        form = await request.form()
        name = str(form.get("name") or "").strip()
        if not name:
            flash(request, "Nome do cliente é obrigatório.", "danger")
            return RedirectResponse("/people", 303)
        try:
            customer.phone = _digits(form.get("phone"))
        except ValueError as exc:
            flash(request, str(exc), "danger")
            return RedirectResponse("/people", 303)
        customer.name = name
        customer.email = _clean(form.get("email"))
        _upsert_customer_address(db, customer.id, form)
        db.commit()
        flash(request, "Cliente atualizado.", "success")
        return RedirectResponse("/people", 303)

    @app.post("/suppliers")
    async def enhanced_supplier_create(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(require_manager),
    ):
        form = await request.form()
        name = str(form.get("name") or "").strip()
        if not name:
            flash(request, "Nome do fornecedor é obrigatório.", "danger")
            return RedirectResponse("/people", 303)
        try:
            phone = _digits(form.get("phone"))
        except ValueError as exc:
            flash(request, str(exc), "danger")
            return RedirectResponse("/people", 303)
        supplier = Supplier(company_id=user.company_id, name=name, contact=None)
        db.add(supplier)
        db.flush()
        db.add(SupplierProfile(supplier_id=supplier.id, phone=phone, email=_clean(form.get("email"))))
        _replace_supplier_materials(db, supplier.id, user.company_id, [str(x) for x in form.getlist("material_ids")])
        db.commit()
        flash(request, "Fornecedor cadastrado com os insumos fornecidos.", "success")
        return RedirectResponse("/people", 303)

    @app.post("/suppliers/{supplier_id}")
    async def enhanced_supplier_update(
        request: Request,
        supplier_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(require_manager),
    ):
        supplier = db.get(Supplier, supplier_id)
        if not supplier or supplier.company_id != user.company_id:
            raise HTTPException(404)
        form = await request.form()
        name = str(form.get("name") or "").strip()
        if not name:
            flash(request, "Nome do fornecedor é obrigatório.", "danger")
            return RedirectResponse("/people", 303)
        try:
            phone = _digits(form.get("phone"))
        except ValueError as exc:
            flash(request, str(exc), "danger")
            return RedirectResponse("/people", 303)
        supplier.name = name
        profile = db.scalar(select(SupplierProfile).where(SupplierProfile.supplier_id == supplier.id))
        if not profile:
            profile = SupplierProfile(supplier_id=supplier.id)
            db.add(profile)
        profile.phone = phone
        profile.email = _clean(form.get("email"))
        supplier.contact = None
        _replace_supplier_materials(db, supplier.id, user.company_id, [str(x) for x in form.getlist("material_ids")])
        db.commit()
        flash(request, "Fornecedor atualizado.", "success")
        return RedirectResponse("/people", 303)
