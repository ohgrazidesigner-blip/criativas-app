from sqlalchemy import select

from app.db import SessionLocal
from app.models import Material, Order, Product, TechnicalSheetItem
from app.roadmap_models import (
    MaterialCategoryAssignment,
    ProductCategoryAssignment,
)
from .conftest import bootstrap_entities


def _post_product(client, path, *, name, category, material_rows, base_price="35"):
    data = {
        "name": name,
        "category": category,
        "base_price": base_price,
        "labor_minutes": "12",
        "expected_loss_rate": "3",
        "standard_lead_time_days": "3",
        "active": "1",
        "material_id": [material_id for material_id, _ in material_rows],
        "quantity": [qty for _, qty in material_rows],
    }
    return client.post(path, data=data, follow_redirects=False)


def test_orders_support_combined_filters(manager):
    data = bootstrap_entities(manager, costs=True, stock=20)
    first = manager.post(
        "/orders/new",
        data={
            "customer_id": data["customer_id"],
            "product_id": data["product_id"],
            "quantity": "1",
            "unit_price": "35",
            "expected_payment_method": "PIX",
            "fulfillment_method": "PICKUP",
            "discount": "0",
            "freight_charged": "0",
            "company_freight_cost": "0",
            "promised_at": "",
            "priority": "NORMAL",
        },
        follow_redirects=False,
    )
    assert first.status_code == 303

    manager.post("/customers", data={"name": "Cliente Filtro", "phone": "71999990000", "email": ""})
    with SessionLocal() as db:
        from app.models import Customer
        second_customer = db.scalar(select(Customer).where(Customer.name == "Cliente Filtro"))

    second = manager.post(
        "/orders/new",
        data={
            "customer_id": second_customer.id,
            "product_id": data["product_id"],
            "quantity": "1",
            "unit_price": "35",
            "expected_payment_method": "CARD",
            "fulfillment_method": "SHIPPING",
            "discount": "0",
            "freight_charged": "0",
            "company_freight_cost": "0",
            "promised_at": "",
            "priority": "NORMAL",
        },
        follow_redirects=False,
    )
    assert second.status_code == 303

    response = manager.get(
        "/orders",
        params={
            "q": "Cliente Filtro",
            "status": "DRAFT",
            "payment_method": "CARD",
            "fulfillment_method": "SHIPPING",
        },
    )
    assert response.status_code == 200
    assert "Cliente Filtro" in response.text
    assert "Cliente Teste" not in response.text


def test_inventory_low_stock_badge_and_filter(manager):
    data = bootstrap_entities(manager, costs=True, stock=5)
    material_id, material_name = data["mats"][0]
    manager.post(
        f"/materials/{material_id}/cost",
        data={"cost": "8.80", "min_stock": "99999"},
        follow_redirects=False,
    )
    response = manager.get("/inventory", params={"low_stock": "1"})
    assert response.status_code == 200
    assert material_name in response.text
    assert "Abaixo do mínimo" in response.text


def test_material_category_is_persisted(manager):
    response = manager.post(
        "/materials/new",
        data={
            "name": "Embalagem teste 2026",
            "category": "Embalagens",
            "unit": "un",
            "current_cost": "1.25",
            "min_stock": "5",
            "active": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        material = db.scalar(select(Material).where(Material.name == "Embalagem teste 2026"))
        assignment = db.get(MaterialCategoryAssignment, material.id)
        assert assignment is not None
        assert assignment.category.name == "Embalagens"



def test_catalog_uses_2026_grouped_view(manager):
    response = manager.get("/catalog")
    assert response.status_code == 200
    assert 'href="/materials/new"' in response.text
    assert 'href="/products/new"' in response.text


def test_product_create_and_edit_support_more_than_three_materials(manager):
    with SessionLocal() as db:
        materials = db.scalars(select(Material).order_by(Material.name)).all()
        assert len(materials) >= 4
        initial_rows = [(material.id, "1") for material in materials[:4]]

    created = _post_product(
        manager,
        "/products",
        name="Kit teste 2026",
        category="Kits e presentes",
        material_rows=initial_rows,
        base_price="70",
    )
    assert created.status_code == 303

    with SessionLocal() as db:
        product = db.scalar(select(Product).where(Product.name == "Kit teste 2026"))
        product_id = product.id
        assignment = db.get(ProductCategoryAssignment, product_id)
        assert assignment.category.name == "Kits e presentes"
        items = db.scalars(
            select(TechnicalSheetItem).where(TechnicalSheetItem.product_id == product_id)
        ).all()
        assert len(items) == 4

    with SessionLocal() as db:
        materials = db.scalars(select(Material).order_by(Material.name)).all()
        edited_rows = [(material.id, "0.5") for material in materials[:4]]

    edited = _post_product(
        manager,
        f"/products/{product_id}/edit",
        name="Kit teste 2026 atualizado",
        category="Kits e presentes",
        material_rows=edited_rows,
        base_price="75",
    )
    assert edited.status_code == 303

    with SessionLocal() as db:
        product = db.get(Product, product_id)
        assert product.name == "Kit teste 2026 atualizado"
        assert str(product.base_price) == "75.00"
        items = db.scalars(
            select(TechnicalSheetItem).where(TechnicalSheetItem.product_id == product_id)
        ).all()
        latest_version = max(item.version for item in items)
        latest_items = [item for item in items if item.version == latest_version]
        assert len(items) == 8
        assert len(latest_items) == 4
        assert latest_version >= 2
