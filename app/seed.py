from __future__ import annotations

import os
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_password
from .models import Company, Material, Product, Role, TechnicalSheetItem, User


def seed(db: Session):
    company = db.scalar(select(Company).limit(1))
    if not company:
        company = Company(name="Criativas")
        db.add(company)
        db.flush()

    app_env = os.getenv("APP_ENV", "production").lower()
    default_password = os.getenv("CRIATIVAS_INITIAL_PASSWORD")
    if not default_password:
        if app_env in {"development", "test"}:
            default_password = "Criativas2024!"
        else:
            raise RuntimeError("CRIATIVAS_INITIAL_PASSWORD is required before the first production startup")
    if app_env == "production" and len(default_password) < 12:
        raise RuntimeError("CRIATIVAS_INITIAL_PASSWORD must have at least 12 characters in production")
    users = [
        ("Gestão Criativas", "admin@criativas.local", Role.MANAGER),
        ("Operação Criativas", "operacao@criativas.local", Role.OPERATIONAL),
    ]
    for name, email, role in users:
        if not db.scalar(select(User).where(User.email == email)):
            db.add(User(company_id=company.id, name=name, email=email, password_hash=hash_password(default_password), role=role))

    # Only known catalog labels are preloaded. Costs and stock intentionally start unknown/zero.
    known = [
        ("Caneca Cerâmica Branca 325ml", "un"),
        ("Caixa para caneca", "un"),
        ("Papel sulfite A4", "folha"),
    ]
    materials = {}
    for name, unit in known:
        m = db.scalar(select(Material).where(Material.company_id == company.id, Material.name == name))
        if not m:
            m = Material(company_id=company.id, name=name, unit=unit, current_cost=None, min_stock=Decimal("0"))
            db.add(m)
            db.flush()
        materials[name] = m

    product = db.scalar(select(Product).where(Product.company_id == company.id, Product.name == "Caneca personalizada"))
    if not product:
        product = Product(company_id=company.id, name="Caneca personalizada", base_price=Decimal("0"), labor_minutes=12, expected_loss_rate=Decimal("0"), standard_lead_time_days=3)
        db.add(product)
        db.flush()
        db.add(TechnicalSheetItem(product_id=product.id, material_id=materials["Caneca Cerâmica Branca 325ml"].id, qty=Decimal("1"), version=1))
        db.add(TechnicalSheetItem(product_id=product.id, material_id=materials["Caixa para caneca"].id, qty=Decimal("1"), version=1))

    db.commit()
