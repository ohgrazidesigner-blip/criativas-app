from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
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
    Supplier,
    User,
)


def _remove_route(app, path: str, method: str) -> None:
    for route in list(app.router.routes):
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            app.router.routes.remove(route)


def _validate_phone(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise ValueError("Telefone deve conter apenas números.")
    return value


def _upsert_customer_address(
    db: Session,
    customer_id: str,
    *,
    address_line: str = "",
    number: str = "",
    complement: str = "",
    neighborhood: str = "",
    city: str = "",
    state: str = "",
    postal_code: str = "",
) -> CustomerAddress:
    address = db.scalar(select(CustomerAddress).where(CustomerAddress.customer_id == customer_id))
    if not address:
        address = CustomerAddress(customer_id=customer_id)
        db.add(address)
    address.address_line = address_line.strip() or None
    address.number = number.strip() or None
    address.complement = complement.strip() or None
    address.neighborhood = neighborhood.strip() or None
    address.city = city.strip() or None
    address.state = state.strip().upper() or None
    address.postal_code = postal_code.strip() or None
    return address


def _upsert_supplier_profile(db: Session, supplier_id: str, phone: str | None, email: str) -> SupplierProfile:
    profile = db.scalar(select(SupplierProfile).where(SupplierProfile.supplier_id == supplier_id))
    if not profile:
        profile = SupplierProfile(supplier_id=supplier_id)
        db.add(profile)
    profile.phone = phone
    profile.email = email.strip().lower() or None
    return profile


def _replace_supplier_materials(db: Session, supplier_id: str, company_id: str, material_ids: list[str]) -> None:
    existing = db.scalars(select(SupplierMaterial).where(SupplierMaterial.supplier_id == supplier_id)).all()
    for link in existing:
        db.delete(link)
    seen: set[str] = set()
    for material_id in material_ids:
        if material_id in seen:
            continue
        material = db.get(Material, material_id)
        if material and material.company_id == company_id:
            db.add(SupplierMaterial(supplier_id=supplier_id, material_id=material.id))
            seen.add(material.id)


def install_runtime_enhancements(main_module) -> None:
    app = main_module.app
    if getattr(app.state, "customer_supplier_enhancements_installed", False):
        return

    _remove_route(app, "/orders/new", "POST")
    _remove_route(app, "/people", "GET")
    _remove_route(app, "/customers", "POST")
    _remove_route(app, "/suppliers", "POST")

    async def people(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_user),
    ):
        customers = db.scalars(select(Customer).where(Customer.company_id == user.company_id).order_by(Customer.name)).all()
        suppliers = db.scalars(select(Supplier).where(Supplier.company_id == user.company_id).order_by(Supplier.name)).all()
        materials = db.scalars(select(Material).where(Material.company_id == user.company_id).order_by(Material.name)).all()
        customer_addresses = {
            address.customer_id: address
            for address in db.scalars(
                select(CustomerAddress).join(Customer).where(Customer.company_id == user.company_id)
            ).all()
        }
        supplier_profiles = {
            profile.supplier_id: profile
            for profile in db.scalars(
                select(SupplierProfile).join(Supplier).where(Supplier.company_id == user.company_id)
            ).all()
        }
        supplier_materials: dict[str, list[str]] = defaultdict(list)
        links = db.scalars(
            select(SupplierMaterial)
            .join(Supplier)
            .where(Supplier.company_id == user.company_id)
            .options(selectinload(SupplierMaterial.material))
        ).all()
        for link in links:
            supplier_materials[link.supplier_id].append(link.material_id)
        return main_module.render(
            request,
            "people.html",
            user=user,
            customers=customers,
            suppliers=suppliers,
            materials=materials,
            customer_addresses=customer_addresses,
            supplier_profiles=supplier_profiles,
            supplier_materials=supplier_materials,
        )

    async def order_create(
        request: Request,
        customer_id: str = Form(...),
        product_id: list[str] = Form(...),
        quantity: list[Decimal] = Form(...),
        unit_price: list[Decimal] = Form(...),
        discount: Decimal = Form(0),
        freight_charged: Decimal = Form(0),
        company_freight_cost: Decimal = Form(0),
        expected_payment_method: PaymentMethod = Form(PaymentMethod.PIX),
        fulfillment_method: FulfillmentMethod = Form(FulfillmentMethod.PICKUP),
        promised_at: str = Form(""),
        priority: str = Form("NORMAL"),
        new_customer_name: str = Form(""),
        new_customer_phone: str = Form(""),
        new_customer_email: str = Form(""),
        address_line: str = Form(""),
        address_number: str = Form(""),
        address_complement: str = Form(""),
        neighborhood: str = Form(""),
        city: str = Form(""),
        state: str = Form(""),
        postal_code: str = Form(""),
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_user),
    ):
        if customer_id == "__new__":
            if not new_customer_name.strip():
                main_module.flash(request, "Informe o nome do novo cliente.", "danger")
                return RedirectResponse("/orders/new", 303)
            try:
                phone = _validate_phone(new_customer_phone)
            except ValueError as exc:
                main_module.flash(request, str(exc), "danger")
                return RedirectResponse("/orders/new", 303)
            customer = Customer(
                company_id=user.company_id,
                name=new_customer_name.strip(),
                phone=phone,
                email=new_customer_email.strip().lower() or None,
            )
            db.add(customer)
            db.flush()
            _upsert_customer_address(
                db,
                customer.id,
                address_line=address_line,
                number=address_number,
                complement=address_complement,
                neighborhood=neighborhood,
                city=city,
                state=state,
                postal_code=postal_code,
            )
            customer_id = customer.id
        else:
            customer = db.get(Customer, customer_id)
            if not customer or customer.company_id != user.company_id:
                raise HTTPException(400)

        promised = datetime.fromisoformat(promised_at).replace(tzinfo=timezone.utc) if promised_at else None
        order = Order(
            company_id=user.company_id,
            number=next_order_number(db),
            customer_id=customer_id,
            discount=discount,
            freight_charged=freight_charged,
            company_freight_cost=company_freight_cost,
            expected_payment_method=expected_payment_method,
            fulfillment_method=fulfillment_method,
            original_promised_at=promised,
            current_promised_at=promised,
            priority=priority,
        )
        db.add(order)
        db.flush()
        item_count = 0
        for pid, qty, price in zip(product_id, quantity, unit_price):
            if D(qty) <= 0:
                continue
            product = db.get(Product, pid)
            if not product or product.company_id != user.company_id:
                continue
            db.add(OrderItem(order_id=order.id, product_id=pid, qty=qty, unit_price=price))
            item_count += 1
        if item_count == 0:
            db.rollback()
            main_module.flash(request, "Adicione ao menos um item válido.", "danger")
            return RedirectResponse("/orders/new", 303)
        db.commit()
        main_module.flash(request, f"Pedido #{order.number} criado como rascunho.", "success")
        return RedirectResponse(f"/orders/{order.id}", 303)

    async def customer_create(
        request: Request,
        name: str = Form(...),
        phone: str = Form(""),
        email: str = Form(""),
        address_line: str = Form(""),
        address_number: str = Form(""),
        address_complement: str = Form(""),
        neighborhood: str = Form(""),
        city: str = Form(""),
        state: str = Form(""),
        postal_code: str = Form(""),
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_user),
    ):
        try:
            normalized_phone = _validate_phone(phone)
        except ValueError as exc:
            main_module.flash(request, str(exc), "danger")
            return RedirectResponse("/people", 303)
        customer = Customer(
            company_id=user.company_id,
            name=name.strip(),
            phone=normalized_phone,
            email=email.strip().lower() or None,
        )
        db.add(customer)
        db.flush()
        _upsert_customer_address(
            db,
            customer.id,
            address_line=address_line,
            number=address_number,
            complement=address_complement,
            neighborhood=neighborhood,
            city=city,
            state=state,
            postal_code=postal_code,
        )
        db.commit()
        main_module.flash(request, "Cliente cadastrado.", "success")
        return RedirectResponse("/people", 303)

    async def customer_update(
        request: Request,
        customer_id: str,
        name: str = Form(...),
        phone: str = Form(""),
        email: str = Form(""),
        address_line: str = Form(""),
        address_number: str = Form(""),
        address_complement: str = Form(""),
        neighborhood: str = Form(""),
        city: str = Form(""),
        state: str = Form(""),
        postal_code: str = Form(""),
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_user),
    ):
        customer = db.get(Customer, customer_id)
        if not customer or customer.company_id != user.company_id:
            raise HTTPException(404)
        try:
            normalized_phone = _validate_phone(phone)
        except ValueError as exc:
            main_module.flash(request, str(exc), "danger")
            return RedirectResponse("/people", 303)
        customer.name = name.strip()
        customer.phone = normalized_phone
        customer.email = email.strip().lower() or None
        _upsert_customer_address(
            db,
            customer.id,
            address_line=address_line,
            number=address_number,
            complement=address_complement,
            neighborhood=neighborhood,
            city=city,
            state=state,
            postal_code=postal_code,
        )
        db.commit()
        main_module.flash(request, "Cliente atualizado.", "success")
        return RedirectResponse("/people", 303)

    async def supplier_create(
        request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            main_module.flash(request, "Nome do fornecedor é obrigatório.", "danger")
            return RedirectResponse("/people", 303)
        try:
            phone = _validate_phone(str(form.get("phone", "")))
        except ValueError as exc:
            main_module.flash(request, str(exc), "danger")
            return RedirectResponse("/people", 303)
        supplier = Supplier(company_id=user.company_id, name=name, contact=None)
        db.add(supplier)
        db.flush()
        _upsert_supplier_profile(db, supplier.id, phone, str(form.get("email", "")))
        _replace_supplier_materials(db, supplier.id, user.company_id, [str(x) for x in form.getlist("material_ids")])
        db.commit()
        main_module.flash(request, "Fornecedor cadastrado.", "success")
        return RedirectResponse("/people", 303)

    async def supplier_update(
        request: Request,
        supplier_id: str,
        db: Session = Depends(get_db),
        user: User = Depends(main_module.require_manager),
    ):
        supplier = db.get(Supplier, supplier_id)
        if not supplier or supplier.company_id != user.company_id:
            raise HTTPException(404)
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            main_module.flash(request, "Nome do fornecedor é obrigatório.", "danger")
            return RedirectResponse("/people", 303)
        try:
            phone = _validate_phone(str(form.get("phone", "")))
        except ValueError as exc:
            main_module.flash(request, str(exc), "danger")
            return RedirectResponse("/people", 303)
        supplier.name = name
        supplier.contact = None
        _upsert_supplier_profile(db, supplier.id, phone, str(form.get("email", "")))
        _replace_supplier_materials(db, supplier.id, user.company_id, [str(x) for x in form.getlist("material_ids")])
        db.commit()
        main_module.flash(request, "Fornecedor atualizado.", "success")
        return RedirectResponse("/people", 303)

    app.add_api_route("/people", people, methods=["GET"], response_class=HTMLResponse, name="enhanced_people")
    app.add_api_route("/orders/new", order_create, methods=["POST"], name="enhanced_order_create")
    app.add_api_route("/customers", customer_create, methods=["POST"], name="enhanced_customer_create")
    app.add_api_route("/customers/{customer_id}", customer_update, methods=["POST"], name="customer_update")
    app.add_api_route("/suppliers", supplier_create, methods=["POST"], name="enhanced_supplier_create")
    app.add_api_route("/suppliers/{supplier_id}", supplier_update, methods=["POST"], name="supplier_update")

    app.state.customer_supplier_enhancements_installed = True
