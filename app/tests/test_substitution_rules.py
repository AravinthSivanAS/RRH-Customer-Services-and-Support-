from __future__ import annotations

from app.models.customer import ProductPreference, SubstitutionRule
from app.repositories.json_repository import data_store
from app.services import next_best_action_service
from app.tests.conftest import CUSTOMER_ID, ORDER_ID, ORIGINAL_SKU, STORE_ID, short_pick_payload


def _meadow_preference(price_increase_limit_percentage: float = 0) -> ProductPreference:
    return ProductPreference(
        product_category="Milk",
        preferred_product_name="Meadow Lactose-Free Milk 1L",
        preferred_brand="Meadow",
        preferred_sku_id=ORIGINAL_SKU,
        substitution_rule=SubstitutionRule.SAME_BRAND_OR_ASK,
        price_increase_limit_percentage=price_increase_limit_percentage,
    )


def test_ranking_enforces_same_brand_same_category_and_price_limit():
    original = data_store.find_product(ORIGINAL_SKU)
    preference = _meadow_preference()

    valid, rejected = next_best_action_service.rank_alternatives(original, preference, STORE_ID)

    valid_brands = {alt.brand for alt in valid}
    assert valid_brands == {"Meadow"}

    rejected_skus = {r.sku_id for r in rejected}
    assert "SKU-YACY-LF-1L" in rejected_skus  # different brand
    assert "SKU-FARM-EGGS-12" not in rejected_skus  # different category: not even considered


def test_price_above_limit_is_rejected():
    original = data_store.find_product(ORIGINAL_SKU)
    product = data_store.find_product("SKU-MEADOW-LF-2L")
    product["unit_price"] = 10.00  # well above the original price
    preference = _meadow_preference(price_increase_limit_percentage=0)

    valid, rejected = next_best_action_service.rank_alternatives(original, preference, STORE_ID)

    valid_skus = {alt.sku_id for alt in valid}
    rejected_skus = {r.sku_id for r in rejected}
    assert "SKU-MEADOW-LF-2L" not in valid_skus
    assert "SKU-MEADOW-LF-2L" in rejected_skus


def test_selecting_an_unoffered_product_is_rejected(client):
    client.post("/api/events/short-pick", json=short_pick_payload())
    notification_id = data_store.substitution_cases[ORDER_ID]["notification_id"]

    response = client.post(
        f"/api/orders/{ORDER_ID}/substitution-selection",
        json={
            "customer_id": CUSTOMER_ID,
            "original_sku_id": ORIGINAL_SKU,
            "selected_sku_id": "SKU-YACY-LF-1L",  # was rejected, never offered
            "notification_id": notification_id,
        },
    )
    assert response.status_code == 400


def test_applying_the_same_substitution_twice_is_rejected(client):
    client.post("/api/events/short-pick", json=short_pick_payload())
    notification_id = data_store.substitution_cases[ORDER_ID]["notification_id"]
    selection_body = {
        "customer_id": CUSTOMER_ID,
        "original_sku_id": ORIGINAL_SKU,
        "selected_sku_id": "SKU-MEADOW-LF-2L",
        "notification_id": notification_id,
    }

    first = client.post(f"/api/orders/{ORDER_ID}/substitution-selection", json=selection_body)
    assert first.status_code == 200

    second = client.post(f"/api/orders/{ORDER_ID}/substitution-selection", json=selection_body)
    assert second.status_code == 409


def test_customer_can_save_a_future_substitution_rule(client):
    response = client.post(
        f"/api/customers/{CUSTOMER_ID}/preferences",
        json={
            "product_category": "Milk",
            "preferred_brand": "Meadow",
            "preferred_sku_id": ORIGINAL_SKU,
            "substitution_rule": "SAME_BRAND_OR_ASK",
            "price_increase_limit_percentage": 5,
        },
    )
    assert response.status_code == 200
    prefs = response.json()["product_preferences"]
    milk_pref = next(p for p in prefs if p["product_category"] == "Milk")
    assert milk_pref["price_increase_limit_percentage"] == 5
