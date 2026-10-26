from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class OrderStatus(str, Enum):
    PICKING = "PICKING"
    PICKED = "PICKED"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class ItemStatus(str, Enum):
    PENDING_PICK = "PENDING_PICK"
    PICKED = "PICKED"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    SUBSTITUTED = "SUBSTITUTED"
    APPROVED_SUBSTITUTION = "APPROVED_SUBSTITUTION"


class OrderItem(BaseModel):
    sku_id: str
    product_name: str
    quantity: int
    unit_price: float
    item_status: ItemStatus
    substitution_reason: Optional[str] = None
    decision_source: Optional[str] = None
    original_sku_id: Optional[str] = None


class Order(BaseModel):
    order_id: str
    customer_id: str
    order_date: str
    delivery_date: str
    delivery_slot: str
    fulfilment_store_id: str
    status: OrderStatus
    payment_status: str
    currency: str
    items: List[OrderItem]

    @property
    def order_total(self) -> float:
        return round(
            sum(
                item.unit_price * item.quantity
                for item in self.items
                if item.item_status != ItemStatus.SUBSTITUTED
            ),
            2,
        )


class SubstitutionSelectionRequest(BaseModel):
    customer_id: str
    original_sku_id: str
    selected_sku_id: str
    notification_id: str
