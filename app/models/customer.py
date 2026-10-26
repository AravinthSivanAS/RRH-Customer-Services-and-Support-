from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SubstitutionRule(str, Enum):
    SAME_BRAND_OR_ASK = "SAME_BRAND_OR_ASK"


class ContactDetails(BaseModel):
    email: str
    mobile: str
    preferred_contact_channel: str
    whatsapp_opt_in: bool
    email_opt_in: bool
    sms_opt_in: bool


class Address(BaseModel):
    address_line_1: str
    city: str
    postal_code: str
    country: str
    latitude: float
    longitude: float


class Household(BaseModel):
    household_size: int
    has_children: bool


class ProductPreference(BaseModel):
    product_category: str
    preferred_product_name: str
    preferred_brand: str
    preferred_sku_id: str
    substitution_rule: SubstitutionRule
    price_increase_limit_percentage: float = 0
    notes: Optional[str] = None


class CustomerPreferences(BaseModel):
    allow_preference_based_recommendations: bool
    allow_substitution_contact: bool
    dietary_requirements: List[str] = Field(default_factory=list)
    product_preferences: List[ProductPreference] = Field(default_factory=list)


class RecentProduct(BaseModel):
    sku_id: str
    product_name: str
    category: str
    brand: str
    last_ordered_date: str
    order_frequency: str
    usual_quantity: int
    preferred: bool


class ActiveOrderItem(BaseModel):
    sku_id: str
    product_name: str
    quantity: int
    unit_price: float
    item_status: str


class ActiveOrderSnapshot(BaseModel):
    order_id: str
    order_date: str
    delivery_date: str
    delivery_slot: str
    fulfilment_store_id: str
    status: str
    payment_status: str
    currency: str
    items: List[ActiveOrderItem]


class Customer360(BaseModel):
    customer_id: str
    customer_name: str
    customer_since: str
    loyalty_tier: str
    contact_details: ContactDetails
    address: Address
    preferred_store_id: str
    household: Household
    preferences: CustomerPreferences
    recent_products: List[RecentProduct] = Field(default_factory=list)
    active_order: Optional[ActiveOrderSnapshot] = None


class ProductPreferenceUpdateRequest(BaseModel):
    product_category: str
    preferred_brand: str
    preferred_sku_id: str
    substitution_rule: SubstitutionRule
    price_increase_limit_percentage: float = 0
    preferred_product_name: Optional[str] = None
    notes: Optional[str] = None
