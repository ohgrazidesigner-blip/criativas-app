from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Material, Product


def test_owner_real_costs_are_seeded_without_overwriting_ambiguous_components(manager):
    with SessionLocal() as db:
        caneca = db.scalar(select(Material).where(Material.name == "Caneca Cerâmica Branca 325ml"))
        camisa = db.scalar(select(Material).where(Material.name == "Camisa poliéster branca M"))
        caixa = db.scalar(select(Material).where(Material.name == "Caixa para caneca"))
        papel = db.scalar(select(Material).where(Material.name == "Papel sublimático A4"))

        assert caneca is not None and Decimal(caneca.current_cost) == Decimal("8.8000")
        assert camisa is not None and Decimal(camisa.current_cost) == Decimal("9.0000")
        assert caixa is not None and Decimal(caixa.current_cost) == Decimal("1.0400")
        # The CSV says Papel A4 cost per finished piece, but it does not identify
        # which physical paper SKU/quantity should be decremented from inventory.
        assert papel is not None and papel.current_cost is None


def test_real_csv_prices_are_visible_as_reference_breakdown(manager):
    response = manager.get("/catalog")
    assert response.status_code == 200
    html = response.text
    assert "Custos reais informados por peça" in html
    assert "Total de insumos informado" in html
    assert "R$ 11,12" in html
    assert "R$ 10,28" in html
    assert "referência ainda sem vínculo físico de estoque" in html


def test_shirt_is_seeded_inactive_until_full_physical_sheet_is_confirmed(manager):
    with SessionLocal() as db:
        caneca = db.scalar(select(Product).where(Product.name == "Caneca personalizada"))
        camisa = db.scalar(select(Product).where(Product.name == "Camisa personalizada"))

        assert caneca is not None and Decimal(caneca.base_price) == Decimal("27.00")
        assert camisa is not None and Decimal(camisa.base_price) == Decimal("25.00")
        assert camisa.active is False
