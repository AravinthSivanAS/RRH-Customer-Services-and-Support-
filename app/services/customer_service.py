from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app.models.customer import Customer360, CustomerPreferences, ProductPreferenceUpdateRequest
from app.repositories.json_repository import data_store


def get_customer(customer_id: str) -> Customer360:
    raw = data_store.find_customer(customer_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Customer '{customer_id}' not found")
    return Customer360.model_validate(raw)


def get_customer_raw(customer_id: str) -> Dict[str, Any]:
    raw = data_store.find_customer(customer_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Customer '{customer_id}' not found")
    return raw


def get_preferences(customer_id: str) -> CustomerPreferences:
    customer = get_customer(customer_id)
    return customer.preferences


def upsert_product_preference(
    customer_id: str, update: ProductPreferenceUpdateRequest
) -> CustomerPreferences:
    raw = get_customer_raw(customer_id)
    prefs = raw["preferences"]["product_preferences"]

    preferred_product_name = update.preferred_product_name
    if preferred_product_name is None:
        product = data_store.find_product(update.preferred_sku_id)
        preferred_product_name = product["product_name"] if product else update.preferred_sku_id

    new_entry = {
        "product_category": update.product_category,
        "preferred_product_name": preferred_product_name,
        "preferred_brand": update.preferred_brand,
        "preferred_sku_id": update.preferred_sku_id,
        "substitution_rule": update.substitution_rule.value,
        "price_increase_limit_percentage": update.price_increase_limit_percentage,
        "notes": update.notes,
    }

    with data_store.lock:
        replaced = False
        for i, pref in enumerate(prefs):
            if pref["product_category"] == update.product_category:
                prefs[i] = new_entry
                replaced = True
                break
        if not replaced:
            prefs.append(new_entry)

    return CustomerPreferences.model_validate(raw["preferences"])
