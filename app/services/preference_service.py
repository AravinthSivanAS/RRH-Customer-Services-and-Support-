from __future__ import annotations

from typing import Any, Dict, Optional

from app.models.customer import Customer360, ProductPreference


def find_product_preference(customer: Customer360, sku_id: str) -> Optional[ProductPreference]:
    for pref in customer.preferences.product_preferences:
        if pref.preferred_sku_id == sku_id:
            return pref
    return None


def find_product_preference_by_category(
    customer: Customer360, category: str
) -> Optional[ProductPreference]:
    for pref in customer.preferences.product_preferences:
        if pref.product_category.lower() == category.lower():
            return pref
    return None
