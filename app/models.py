from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uuid4() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    MANAGER = "MANAGER"
    OPERATIONAL = "OPERATIONAL"


class CommercialStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class ProductionStatus(str, enum.Enum):
    PLANNED = "PLANNED"
    READY = "READY"
    BLOCKED = "BLOCKED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class FulfillmentMethod(str, enum.Enum):
    PICKUP = "PICKUP"
    LOCAL_DELIVERY = "LOCAL_DELIVERY"
    SHIPPING = "SHIPPING"
    OTHER = "OTHER"


class FulfillmentStatus(str, enum.Enum):
    WAITING_PRODUCTION = "WAITING_PRODUCTION"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    READY_FOR_DELIVERY = "READY_FOR_DELIVERY"
    READY_TO_SHIP = "READY_TO_SHIP"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    POSTED = "POSTED"
    PICKED_UP = "PICKED_UP"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class InventoryTxType(str, enum.Enum):
    PURCHASE_IN = "PURCHASE_IN"
    PRODUCTION_OUT = "PRODUCTION_OUT"
    LOSS_OUT = "LOSS_OUT"
    TEST_OUT = "TEST_OUT"
    GIFT_OUT = "GIFT_OUT"
    ADJUSTMENT_IN = "ADJUSTMENT_IN"
    ADJUSTMENT_OUT = "ADJUSTMENT_OUT"
    RETURN_IN = "RETURN_IN"


class ExpenseKind(str, enum.Enum):
    BUSINESS = "BUSINESS"
    PERSONAL_WITHDRAWAL = "PERSONAL_WITHDRAWAL"


class PaymentMethod(str, enum.Enum):
    PIX = "PIX"
    CARD = "CARD"
    CASH = "CASH"
    OTHER = "OTHER"


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), default="Criativas")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Bahia")
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    target_margin: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0.35"))
    hourly_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("20.00"))
    allocable_fixed_monthly: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("1280.00"))
    productive_minutes_month: Mapped[int] = mapped_column(Integer, default=4800)
    pix_fee_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0"))
    card_fee_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0.0499"))
    risk_window_hours: Mapped[int] = mapped_column(Integer, default=4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    company: Mapped[Company] = relationship()


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    contact: Mapped[str | None] = mapped_column(String(180), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(24), default="un")
    current_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    min_stock: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    labor_minutes: Mapped[int] = mapped_column(Integer, default=0)
    expected_loss_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0"))
    standard_lead_time_days: Mapped[int] = mapped_column(Integer, default=3)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    technical_items: Mapped[list["TechnicalSheetItem"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class TechnicalSheetItem(Base):
    __tablename__ = "technical_sheet_items"
    __table_args__ = (UniqueConstraint("product_id", "material_id", "version", name="uq_sheet_material_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    version: Mapped[int] = mapped_column(Integer, default=1)
    product: Mapped[Product] = relationship(back_populates="technical_items")
    material: Mapped[Material] = relationship()


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    commercial_status: Mapped[CommercialStatus] = mapped_column(Enum(CommercialStatus), default=CommercialStatus.DRAFT)
    priority: Mapped[str] = mapped_column(String(12), default="NORMAL")
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    freight_charged: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    company_freight_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    expected_payment_method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod), default=PaymentMethod.PIX)
    fulfillment_method: Mapped[FulfillmentMethod] = mapped_column(Enum(FulfillmentMethod), default=FulfillmentMethod.PICKUP)
    original_promised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_promised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    margin_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    customer: Mapped[Customer] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    snapshots: Mapped[list["CostSnapshot"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    production: Mapped["ProductionOrder | None"] = relationship(back_populates="order", uselist=False, cascade="all, delete-orphan")
    fulfillment: Mapped["Fulfillment | None"] = relationship(back_populates="order", uselist=False, cascade="all, delete-orphan")
    promise_changes: Mapped[list["PromiseChange"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    reversed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    order: Mapped[Order] = relationship(back_populates="payments")


class CostSnapshot(Base):
    __tablename__ = "cost_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    order_item_id: Mapped[str] = mapped_column(ForeignKey("order_items.id"))
    materials_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    labor_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    fixed_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    expected_loss_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    integrity: Mapped[str] = mapped_column(String(16), default="COMPLETE")
    confidence: Mapped[str] = mapped_column(String(16), default="MEASURED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    order: Mapped[Order] = relationship(back_populates="snapshots")
    order_item: Mapped[OrderItem] = relationship()


class Purchase(Base):
    __tablename__ = "purchases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    freight: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(24), default="CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    supplier: Mapped[Supplier] = relationship()
    items: Mapped[list["PurchaseItem"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")
    receipts: Mapped[list["GoodsReceipt"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    purchase: Mapped[Purchase] = relationship(back_populates="items")
    material: Mapped[Material] = relationship()


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    purchase: Mapped[Purchase] = relationship(back_populates="receipts")
    items: Mapped[list["GoodsReceiptItem"]] = relationship(back_populates="receipt", cascade="all, delete-orphan")


class GoodsReceiptItem(Base):
    __tablename__ = "goods_receipt_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    receipt_id: Mapped[str] = mapped_column(ForeignKey("goods_receipts.id"), index=True)
    purchase_item_id: Mapped[str] = mapped_column(ForeignKey("purchase_items.id"))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    receipt: Mapped[GoodsReceipt] = relationship(back_populates="items")
    purchase_item: Mapped[PurchaseItem] = relationship()


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), index=True)
    tx_type: Mapped[InventoryTxType] = mapped_column(Enum(InventoryTxType))
    qty_signed: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    material: Mapped[Material] = relationship()


class ProductionOrder(Base):
    __tablename__ = "production_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    status: Mapped[ProductionStatus] = mapped_column(Enum(ProductionStatus), default=ProductionStatus.PLANNED)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    order: Mapped[Order] = relationship(back_populates="production")
    requirements: Mapped[list["MaterialRequirement"]] = relationship(back_populates="production", cascade="all, delete-orphan")
    reservations: Mapped[list["InventoryReservation"]] = relationship(back_populates="production", cascade="all, delete-orphan")
    exceptions: Mapped[list["ProductionException"]] = relationship(back_populates="production", cascade="all, delete-orphan")


class MaterialRequirement(Base):
    __tablename__ = "material_requirements"
    __table_args__ = (UniqueConstraint("production_id", "material_id", name="uq_production_material"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    production_id: Mapped[str] = mapped_column(ForeignKey("production_orders.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), index=True)
    required_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    production: Mapped[ProductionOrder] = relationship(back_populates="requirements")
    material: Mapped[Material] = relationship()


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (UniqueConstraint("production_id", "material_id", name="uq_reservation_production_material"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    production_id: Mapped[str] = mapped_column(ForeignKey("production_orders.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    production: Mapped[ProductionOrder] = relationship(back_populates="reservations")
    material: Mapped[Material] = relationship()


class ProductionException(Base):
    __tablename__ = "production_exceptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    production_id: Mapped[str] = mapped_column(ForeignKey("production_orders.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"))
    kind: Mapped[str] = mapped_column(String(16))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    production: Mapped[ProductionOrder] = relationship(back_populates="exceptions")
    material: Mapped[Material] = relationship()


class PromiseChange(Base):
    __tablename__ = "promise_changes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    previous_promised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    new_promised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    order: Mapped[Order] = relationship(back_populates="promise_changes")


class Fulfillment(Base):
    __tablename__ = "fulfillments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    method: Mapped[FulfillmentMethod] = mapped_column(Enum(FulfillmentMethod))
    status: Mapped[FulfillmentStatus] = mapped_column(Enum(FulfillmentStatus), default=FulfillmentStatus.WAITING_PRODUCTION)
    original_promised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_promised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    order: Mapped[Order] = relationship(back_populates="fulfillment")
    attempts: Mapped[list["DeliveryAttempt"]] = relationship(back_populates="fulfillment", cascade="all, delete-orphan")


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    fulfillment_id: Mapped[str] = mapped_column(ForeignKey("fulfillments.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="STARTED")
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fulfillment: Mapped[Fulfillment] = relationship(back_populates="attempts")


class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    kind: Mapped[ExpenseKind] = mapped_column(Enum(ExpenseKind))
    description: Mapped[str] = mapped_column(String(220))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(36))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
