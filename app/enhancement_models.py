from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .models import Customer, Material, Supplier, uuid4


class CustomerAddress(Base):
    __tablename__ = "customer_addresses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), unique=True, index=True)
    address_line: Mapped[str | None] = mapped_column(String(220), nullable=True)
    number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    complement: Mapped[str | None] = mapped_column(String(120), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(16), nullable=True)

    customer: Mapped[Customer] = relationship()


class SupplierProfile(Base):
    __tablename__ = "supplier_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(180), nullable=True)

    supplier: Mapped[Supplier] = relationship()


class SupplierMaterial(Base):
    __tablename__ = "supplier_materials"
    __table_args__ = (UniqueConstraint("supplier_id", "material_id", name="uq_supplier_material"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), index=True)

    supplier: Mapped[Supplier] = relationship()
    material: Mapped[Material] = relationship()
