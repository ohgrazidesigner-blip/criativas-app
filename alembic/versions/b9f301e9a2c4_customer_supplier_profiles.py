"""customer addresses and supplier material links

Revision ID: b9f301e9a2c4
Revises: 74eabd402284
Create Date: 2026-08-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b9f301e9a2c4"
down_revision: Union[str, None] = "74eabd402284"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_addresses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=False),
        sa.Column("address_line", sa.String(length=220), nullable=True),
        sa.Column("number", sa.String(length=40), nullable=True),
        sa.Column("complement", sa.String(length=120), nullable=True),
        sa.Column("neighborhood", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=True),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id"),
    )
    op.create_index(op.f("ix_customer_addresses_customer_id"), "customer_addresses", ["customer_id"], unique=True)

    op.create_table(
        "supplier_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("email", sa.String(length=180), nullable=True),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("supplier_id"),
    )
    op.create_index(op.f("ix_supplier_profiles_supplier_id"), "supplier_profiles", ["supplier_id"], unique=True)

    op.create_table(
        "supplier_materials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("material_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("supplier_id", "material_id", name="uq_supplier_material"),
    )
    op.create_index(op.f("ix_supplier_materials_material_id"), "supplier_materials", ["material_id"], unique=False)
    op.create_index(op.f("ix_supplier_materials_supplier_id"), "supplier_materials", ["supplier_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_supplier_materials_supplier_id"), table_name="supplier_materials")
    op.drop_index(op.f("ix_supplier_materials_material_id"), table_name="supplier_materials")
    op.drop_table("supplier_materials")
    op.drop_index(op.f("ix_supplier_profiles_supplier_id"), table_name="supplier_profiles")
    op.drop_table("supplier_profiles")
    op.drop_index(op.f("ix_customer_addresses_customer_id"), table_name="customer_addresses")
    op.drop_table("customer_addresses")
