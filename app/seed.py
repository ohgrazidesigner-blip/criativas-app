from __future__ import annotations

import os
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_password
from .enhancement_models import SupplierMaterial
from .models import Company, Material, Product, Role, Supplier, TechnicalSheetItem, User
from .real_costs import REAL_PRODUCT_COST_REFERENCES, VERIFIED_CURRENT_MATERIAL_COSTS


# Most names and supplier relationships below come from the legacy Criativas
# workbook (COMPRAS / ESTOQUE / TABELA DE PREÇO). Papel sulfite A4 is retained
# as an operational material already used by existing production flows. No old
# stock balance or historical price is imported as current truth for any item.
LEGACY_MATERIALS = [
    ("Tecido Sublimático OBM A4", "folha"),
    ("Fita térmica 3300x5 mm", "rolo"),
    ("Power Film V4 50x100 cm", "folha"),
    ("Caneca colorida preta 325ml", "un"),
    ("Caneca colorida rosa 325ml", "un"),
    ("Caneca mágica preta 325ml", "un"),
    ("Caixa para caneca", "un"),
    ("Papel sublimático A4", "folha"),
    ("Papel sulfite A4", "folha"),
    ("Tinta sublimática CMYK 100ml", "frasco"),
    ("Caneca Cerâmica Branca 325ml", "un"),
    ("Caneca polímero branca 325ml", "un"),
    ("Camisa poliéster branca M", "un"),
    ("Caixa para caneca com cola", "un"),
    ("Regata algodão infantil preta 4 anos", "un"),
    ("Camisa algodão infantil preta 4 anos", "un"),
    ("Camisa algodão adulta branca G", "un"),
    ("Camisa algodão adulta preta GG", "un"),
    ("Baby look algodão adulta preta M", "un"),
    ("Regata algodão adulta preta P", "un"),
    ("Camisa algodão adulta branca GG", "un"),
    ("Caneca porcelana branca 325ml", "un"),
    ("Caixa caneca janela", "un"),
    ("Sandália branca 37/38–41/42", "par"),
    ("OBM Power Film Toque Zero A4", "folha"),
    ("Tinta sublimática preta 100ml", "frasco"),
]

# Supplier/material links are intentionally limited to relationships explicitly
# supported by the legacy COMPRAS sheet. Historical prices and balances are not
# promoted to current truth because the workbook contains old/ambiguous values.
LEGACY_SUPPLIER_MATERIALS = {
    "Paint Color": [
        "Tecido Sublimático OBM A4",
        "Fita térmica 3300x5 mm",
        "Power Film V4 50x100 cm",
        "Caneca colorida preta 325ml",
        "Caneca colorida rosa 325ml",
        "Caneca mágica preta 325ml",
        "Caixa para caneca",
    ],
    "Economizou": ["Papel sublimático A4", "Tinta sublimática CMYK 100ml"],
    "Provideo": ["Caneca Cerâmica Branca 325ml"],
    "Cia do Silk": ["Caneca Cerâmica Branca 325ml", "Caneca polímero branca 325ml", "Caixa para caneca com cola"],
    "Atacado das Camisas": ["Camisa poliéster branca M"],
    "Kasa Andrade": [
        "Regata algodão infantil preta 4 anos",
        "Camisa algodão infantil preta 4 anos",
        "Camisa algodão adulta branca G",
        "Camisa algodão adulta preta GG",
        "Baby look algodão adulta preta M",
        "Regata algodão adulta preta P",
        "Camisa algodão adulta branca GG",
    ],
    "Hiper Mídia": ["Caneca porcelana branca 325ml", "Caixa caneca janela", "Sandália branca 37/38–41/42"],
    "Akikola (M. Livre)": ["OBM Power Film Toque Zero A4"],
    "Distrimix": ["Fita térmica 3300x5 mm", "Tinta sublimática preta 100ml"],
}


def _ensure_sheet_item(db: Session, product: Product, material: Material, quantity: Decimal = Decimal("1")) -> None:
    exists = db.scalar(
        select(TechnicalSheetItem).where(
            TechnicalSheetItem.product_id == product.id,
            TechnicalSheetItem.material_id == material.id,
            TechnicalSheetItem.version == 1,
        )
    )
    if not exists:
        db.add(
            TechnicalSheetItem(
                product_id=product.id,
                material_id=material.id,
                qty=quantity,
                version=1,
            )
        )


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

    materials: dict[str, Material] = {}
    for name, unit in LEGACY_MATERIALS:
        material = db.scalar(select(Material).where(Material.company_id == company.id, Material.name == name))
        if not material:
            material = Material(company_id=company.id, name=name, unit=unit, current_cost=None, min_stock=Decimal("0"))
            db.add(material)
            db.flush()
        materials[name] = material

    # Owner-provided 2026 CSV values are more current than the legacy workbook.
    # Apply them only while current_cost is still empty, so future receipts/manual
    # corrections are never overwritten at application startup.
    for material_name, real_cost in VERIFIED_CURRENT_MATERIAL_COSTS.items():
        material = materials.get(material_name)
        if material is not None and material.current_cost is None:
            material.current_cost = real_cost

    for supplier_name, material_names in LEGACY_SUPPLIER_MATERIALS.items():
        supplier = db.scalar(select(Supplier).where(Supplier.company_id == company.id, Supplier.name == supplier_name))
        if not supplier:
            supplier = Supplier(company_id=company.id, name=supplier_name, contact=None)
            db.add(supplier)
            db.flush()
        for material_name in material_names:
            material = materials[material_name]
            exists = db.scalar(
                select(SupplierMaterial).where(
                    SupplierMaterial.supplier_id == supplier.id,
                    SupplierMaterial.material_id == material.id,
                )
            )
            if not exists:
                db.add(SupplierMaterial(supplier_id=supplier.id, material_id=material.id))

    # Caneca is an active product already represented in the operational MVP.
    caneca_ref = REAL_PRODUCT_COST_REFERENCES["Caneca personalizada"]
    product = db.scalar(select(Product).where(Product.company_id == company.id, Product.name == "Caneca personalizada"))
    if not product:
        product = Product(
            company_id=company.id,
            name="Caneca personalizada",
            base_price=caneca_ref["sale_price"],
            labor_minutes=12,
            expected_loss_rate=Decimal("0"),
            standard_lead_time_days=3,
        )
        db.add(product)
        db.flush()
    elif Decimal(product.base_price or 0) == 0:
        product.base_price = caneca_ref["sale_price"]

    _ensure_sheet_item(db, product, materials["Caneca Cerâmica Branca 325ml"])
    _ensure_sheet_item(db, product, materials["Caixa para caneca"])

    # The real CSV also supports a shirt product and its base material cost.
    # It starts inactive because the source does not provide a trustworthy full
    # physical consumption sheet (paper/tinta quantities) or labor time.
    camisa_ref = REAL_PRODUCT_COST_REFERENCES["Camisa personalizada"]
    camisa = db.scalar(select(Product).where(Product.company_id == company.id, Product.name == "Camisa personalizada"))
    if not camisa:
        camisa = Product(
            company_id=company.id,
            name="Camisa personalizada",
            base_price=camisa_ref["sale_price"],
            labor_minutes=0,
            expected_loss_rate=Decimal("0"),
            standard_lead_time_days=3,
            active=False,
        )
        db.add(camisa)
        db.flush()
        _ensure_sheet_item(db, camisa, materials["Camisa poliéster branca M"])
    elif Decimal(camisa.base_price or 0) == 0:
        camisa.base_price = camisa_ref["sale_price"]

    db.commit()

    # Install production-facing refinements after the core FastAPI routes exist.
    main_module = sys.modules.get("app.main")
    if main_module is not None:
        from .runtime_enhancements import install_runtime_enhancements
        from .dashboard_enhancements import install_dashboard_enhancements
        from .catalog_enhancements import install_catalog_enhancements

        install_runtime_enhancements(main_module)
        install_dashboard_enhancements(main_module)
        install_catalog_enhancements(main_module)
