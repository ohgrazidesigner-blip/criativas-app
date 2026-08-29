"""roadmap 2026 phase 1 categories

Revision ID: c42f2026p1a1
Revises: b9f301e9a2c4
Create Date: 2026-08-29
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c42f2026p1a1"
down_revision: Union[str, None] = "b9f301e9a2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_CATEGORIES = ("Canecas", "Camisetas", "Sublimação", "Embalagens")


def _guess_product_category(name: str) -> str | None:
    lowered = name.casefold()
    if "caneca" in lowered:
        return "Canecas"
    if "camisa" in lowered or "camiseta" in lowered:
        return "Camisetas"
    return None


def _guess_material_category(name: str) -> str | None:
    lowered = name.casefold()
    if "caixa" in lowered or "embalag" in lowered:
        return "Embalagens"
    if "papel" in lowered or "tinta" in lowered or "sublim" in lowered or "fita térmica" in lowered or "fita termica" in lowered:
        return "Sublimação"
    if "caneca" in lowered:
        return "Canecas"
    if "camisa" in lowered or "camiseta" in lowered:
        return "Camisetas"
    return None


def upgrade() -> None:
    op.create_table(
        "catalog_categories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "name", name="uq_catalog_category_company_name"),
    )
    op.create_index(
        op.f("ix_catalog_categories_company_id"),
        "catalog_categories",
        ["company_id"],
        unique=False,
    )

    op.create_table(
        "material_category_assignments",
        sa.Column("material_id", sa.String(length=36), nullable=False),
        sa.Column("category_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["catalog_categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("material_id"),
    )
    op.create_index(
        op.f("ix_material_category_assignments_category_id"),
        "material_category_assignments",
        ["category_id"],
        unique=False,
    )

    op.create_table(
        "product_category_assignments",
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("category_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["catalog_categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("product_id"),
    )
    op.create_index(
        op.f("ix_product_category_assignments_category_id"),
        "product_category_assignments",
        ["category_id"],
        unique=False,
    )

    # Existing production data is categorized conservatively by names already
    # present in the 2024 system. Unknown records remain explicitly uncategorized.
    bind = op.get_bind()
    companies = [row[0] for row in bind.execute(sa.text("SELECT id FROM companies")).fetchall()]
    for company_id in companies:
        category_ids: dict[str, str] = {}
        for name in DEFAULT_CATEGORIES:
            category_id = str(uuid.uuid4())
            bind.execute(
                sa.text(
                    "INSERT INTO catalog_categories (id, company_id, name, active) "
                    "VALUES (:id, :company_id, :name, :active)"
                ),
                {"id": category_id, "company_id": company_id, "name": name, "active": True},
            )
            category_ids[name] = category_id

        products = bind.execute(
            sa.text("SELECT id, name FROM products WHERE company_id = :company_id"),
            {"company_id": company_id},
        ).fetchall()
        for product_id, name in products:
            category = _guess_product_category(name)
            if category:
                bind.execute(
                    sa.text(
                        "INSERT INTO product_category_assignments (product_id, category_id) "
                        "VALUES (:product_id, :category_id)"
                    ),
                    {"product_id": product_id, "category_id": category_ids[category]},
                )

        materials = bind.execute(
            sa.text("SELECT id, name FROM materials WHERE company_id = :company_id"),
            {"company_id": company_id},
        ).fetchall()
        for material_id, name in materials:
            category = _guess_material_category(name)
            if category:
                bind.execute(
                    sa.text(
                        "INSERT INTO material_category_assignments (material_id, category_id) "
                        "VALUES (:material_id, :category_id)"
                    ),
                    {"material_id": material_id, "category_id": category_ids[category]},
                )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_product_category_assignments_category_id"),
        table_name="product_category_assignments",
    )
    op.drop_table("product_category_assignments")
    op.drop_index(
        op.f("ix_material_category_assignments_category_id"),
        table_name="material_category_assignments",
    )
    op.drop_table("material_category_assignments")
    op.drop_index(op.f("ix_catalog_categories_company_id"), table_name="catalog_categories")
    op.drop_table("catalog_categories")
