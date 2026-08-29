from __future__ import annotations

from decimal import Decimal


# Aggregated values supplied by the owner in insumos_precos_reais.csv.
# The source file itself is intentionally not committed because it contains
# customer/order data. Only the cost facts needed by the product are retained.
REAL_PRODUCT_COST_REFERENCES = {
    "Caneca personalizada": {
        "source_product": "Caneca Ceramica",
        "sale_price": Decimal("27.00"),
        "components": [
            ("Insumo base", Decimal("8.80"), "Caneca Cerâmica Branca 325ml"),
            ("Papel A4", Decimal("0.38"), None),
            ("Tinta/Arte", Decimal("0.90"), None),
            ("Caixa", Decimal("1.04"), "Caixa para caneca"),
        ],
        "material_total": Decimal("11.12"),
    },
    "Camisa personalizada": {
        "source_product": "Camisa Poliester",
        "sale_price": Decimal("25.00"),
        "components": [
            ("Insumo base", Decimal("9.00"), "Camisa poliéster branca M"),
            ("Papel A4", Decimal("0.38"), None),
            ("Tinta/Arte", Decimal("0.90"), None),
            ("Caixa", Decimal("0.00"), None),
        ],
        "material_total": Decimal("10.28"),
    },
}

# These mappings are direct enough to promote to the current material catalog.
# They are only applied when the material still has no current cost, so a later
# purchase receipt or manual correction always wins.
VERIFIED_CURRENT_MATERIAL_COSTS = {
    "Caneca Cerâmica Branca 325ml": Decimal("8.80"),
    "Camisa poliéster branca M": Decimal("9.00"),
    "Caixa para caneca": Decimal("1.04"),
}

# Papel A4 and Tinta/Arte remain cost references only. The CSV gives their cost
# per finished piece, but not an unambiguous inventory SKU + physical consumption
# quantity. They must not create fake stock requirements.
UNMAPPED_REFERENCE_COMPONENTS = {"Papel A4", "Tinta/Arte"}
