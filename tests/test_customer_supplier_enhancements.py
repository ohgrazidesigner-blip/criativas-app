from sqlalchemy import select

from app.db import SessionLocal
from app.enhancement_models import CustomerAddress, SupplierMaterial, SupplierProfile
from app.models import Customer, Material, Product, Supplier


def test_new_order_can_create_and_save_customer_inline(manager):
    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.name == "Caneca personalizada"))
        assert product is not None
        product_id = product.id

    r = manager.post(
        "/orders/new",
        data={
            "customer_id": "__new__",
            "new_customer_name": "Cliente Inline",
            "new_customer_phone": "71999999999",
            "new_customer_email": "inline@example.com",
            "address_line": "Rua das Flores",
            "address_number": "10",
            "neighborhood": "Centro",
            "city": "Salvador",
            "state": "BA",
            "postal_code": "40000000",
            "product_id": [product_id],
            "quantity": ["1"],
            "unit_price": ["30.00"],
            "discount": "0",
            "freight_charged": "0",
            "company_freight_cost": "0",
            "expected_payment_method": "PIX",
            "fulfillment_method": "LOCAL_DELIVERY",
            "promised_at": "",
            "priority": "NORMAL",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/orders/" in r.headers["location"]

    with SessionLocal() as db:
        customer = db.scalar(select(Customer).where(Customer.name == "Cliente Inline"))
        assert customer is not None
        assert customer.phone == "71999999999"
        address = db.scalar(select(CustomerAddress).where(CustomerAddress.customer_id == customer.id))
        assert address is not None
        assert address.city == "Salvador"
        assert address.address_line == "Rua das Flores"


def test_customer_can_be_edited_and_phone_rejects_text(manager):
    manager.post(
        "/customers",
        data={
            "name": "Cliente Editável",
            "phone": "71911112222",
            "email": "antes@example.com",
            "address_line": "Rua A",
            "city": "Salvador",
        },
    )
    with SessionLocal() as db:
        customer = db.scalar(select(Customer).where(Customer.name == "Cliente Editável"))
        assert customer is not None
        customer_id = customer.id

    r = manager.post(
        f"/customers/{customer_id}",
        data={
            "name": "Cliente Atualizado",
            "phone": "abc123",
            "email": "depois@example.com",
            "address_line": "Rua B",
            "city": "Lauro de Freitas",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with SessionLocal() as db:
        customer = db.get(Customer, customer_id)
        assert customer.name == "Cliente Editável"
        assert customer.phone == "71911112222"

    manager.post(
        f"/customers/{customer_id}",
        data={
            "name": "Cliente Atualizado",
            "phone": "71933334444",
            "email": "depois@example.com",
            "address_line": "Rua B",
            "city": "Lauro de Freitas",
        },
    )
    with SessionLocal() as db:
        customer = db.get(Customer, customer_id)
        assert customer.name == "Cliente Atualizado"
        address = db.scalar(select(CustomerAddress).where(CustomerAddress.customer_id == customer_id))
        assert address.city == "Lauro de Freitas"


def test_supplier_has_phone_email_and_materials(manager):
    with SessionLocal() as db:
        material = db.scalar(select(Material).where(Material.name == "Caneca porcelana branca 325ml"))
        assert material is not None
        material_id = material.id

    r = manager.post(
        "/suppliers",
        data={
            "name": "Fornecedor Novo",
            "phone": "7133334444",
            "email": "fornecedor@example.com",
            "material_ids": [material_id],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    with SessionLocal() as db:
        supplier = db.scalar(select(Supplier).where(Supplier.name == "Fornecedor Novo"))
        assert supplier is not None
        profile = db.scalar(select(SupplierProfile).where(SupplierProfile.supplier_id == supplier.id))
        assert profile.phone == "7133334444"
        assert profile.email == "fornecedor@example.com"
        link = db.scalar(
            select(SupplierMaterial).where(
                SupplierMaterial.supplier_id == supplier.id,
                SupplierMaterial.material_id == material_id,
            )
        )
        assert link is not None


def test_legacy_materials_and_supplier_links_are_seeded(manager):
    with SessionLocal() as db:
        material = db.scalar(select(Material).where(Material.name == "Tecido Sublimático OBM A4"))
        supplier = db.scalar(select(Supplier).where(Supplier.name == "Paint Color"))
        assert material is not None
        assert supplier is not None
        assert material.current_cost is None
        link = db.scalar(
            select(SupplierMaterial).where(
                SupplierMaterial.supplier_id == supplier.id,
                SupplierMaterial.material_id == material.id,
            )
        )
        assert link is not None
