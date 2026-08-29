from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .models import Material, Product, utcnow, uuid4
from .db import Base


class CatalogCategory(Base):
    __tablename__ = "catalog_categories"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_catalog_category_company_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MaterialCategoryAssignment(Base):
    __tablename__ = "material_category_assignments"

    material_id: Mapped[str] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_categories.id", ondelete="CASCADE"), index=True
    )

    material: Mapped[Material] = relationship()
    category: Mapped[CatalogCategory] = relationship()


class ProductCategoryAssignment(Base):
    __tablename__ = "product_category_assignments"

    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("catalog_categories.id", ondelete="CASCADE"), index=True
    )

    product: Mapped[Product] = relationship()
    category: Mapped[CatalogCategory] = relationship()
