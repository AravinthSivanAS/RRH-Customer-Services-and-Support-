from __future__ import annotations

from app.repositories.json_repository import data_store
from app.tests.conftest import CUSTOMER_ID, ORDER_ID, ORIGINAL_SKU, STORE_ID, short_pick_payload


def test_original_milk_is_out_of_stock_at_leeds_central(client):
    response = client.get(f"/api/inventory/{ORIGINAL_SKU}/stores/{STORE_ID}")
    assert response.status_code == 200
    assert response.json()["available_quantity"] == 0


def test_short_pick_identifies_two_same_brand_alternatives(client):
    response = client.post("/api/events/short-pick", json=short_pick_payload())
    assert response.status_code == 200
    body = response.json()

    assert body["decision"]["outcome"] == "CUSTOMER_SELECTION_REQUIRED"
    valid_skus = {alt["sku_id"] for alt in body["decision"]["valid_alternatives"]}
    assert valid_skus == {"SKU-MEADOW-LF-2L", "SKU-MEADOW-LF-6X200ML"}
    assert body["notification"]["status"] == "SENT"
    assert body["next_action"] == "WAIT_FOR_CUSTOMER_SELECTION"


def test_different_brand_milk_is_rejected(client):
    response = client.post("/api/events/short-pick", json=short_pick_payload())
    body = response.json()

    rejected = {r["sku_id"]: r for r in body["decision"]["rejected_alternatives"]}
    assert "SKU-YACY-LF-1L" in rejected
    assert "brand" in rejected["SKU-YACY-LF-1L"]["reason"].lower()


def test_product_with_insufficient_stock_is_not_recommended(client):
    # Simulate the 2L option also being out of stock at the fulfilment store.
    product = data_store.find_product("SKU-MEADOW-LF-2L")
    product["stock_by_store"]["Leeds Central"] = 0

    response = client.post("/api/events/short-pick", json=short_pick_payload())
    body = response.json()

    valid_skus = {alt["sku_id"] for alt in body["decision"]["valid_alternatives"]}
    rejected_skus = {r["sku_id"] for r in body["decision"]["rejected_alternatives"]}
    assert "SKU-MEADOW-LF-2L" not in valid_skus
    assert "SKU-MEADOW-LF-2L" in rejected_skus
    assert valid_skus == {"SKU-MEADOW-LF-6X200ML"}


def test_customer_selection_updates_the_order(client):
    short_pick_response = client.post("/api/events/short-pick", json=short_pick_payload())
    notification_id = short_pick_response.json()["notification"]["notification_id"]

    selection_response = client.post(
        f"/api/orders/{ORDER_ID}/substitution-selection",
        json={
            "customer_id": CUSTOMER_ID,
            "original_sku_id": ORIGINAL_SKU,
            "selected_sku_id": "SKU-MEADOW-LF-2L",
            "notification_id": notification_id,
        },
    )
    assert selection_response.status_code == 200
    order = selection_response.json()

    items_by_sku = {item["sku_id"]: item for item in order["items"]}
    assert items_by_sku[ORIGINAL_SKU]["item_status"] == "SUBSTITUTED"
    assert items_by_sku["SKU-MEADOW-LF-2L"]["item_status"] == "APPROVED_SUBSTITUTION"
    assert items_by_sku["SKU-MEADOW-LF-2L"]["original_sku_id"] == ORIGINAL_SKU
    # Same price, so the order total is unchanged.
    assert order["order_total"] == round(3.49 + 1.50 + 2.90, 2)


def test_selected_product_stock_decreases(client):
    client.post("/api/events/short-pick", json=short_pick_payload())
    before = client.get(f"/api/inventory/SKU-MEADOW-LF-2L/stores/{STORE_ID}").json()
    assert before["available_quantity"] == 7

    notification_id = data_store.substitution_cases[ORDER_ID]["notification_id"]
    client.post(
        f"/api/orders/{ORDER_ID}/substitution-selection",
        json={
            "customer_id": CUSTOMER_ID,
            "original_sku_id": ORIGINAL_SKU,
            "selected_sku_id": "SKU-MEADOW-LF-2L",
            "notification_id": notification_id,
        },
    )

    after = client.get(f"/api/inventory/SKU-MEADOW-LF-2L/stores/{STORE_ID}").json()
    assert after["available_quantity"] == 6


def test_duplicate_short_pick_events_are_rejected_safely(client):
    first = client.post("/api/events/short-pick", json=short_pick_payload())
    assert first.status_code == 200

    second = client.post("/api/events/short-pick", json=short_pick_payload())
    assert second.status_code == 409


def test_stale_inventory_returns_409(client):
    short_pick_response = client.post("/api/events/short-pick", json=short_pick_payload())
    notification_id = short_pick_response.json()["notification"]["notification_id"]

    # Inventory changes underneath the offer before the customer confirms.
    product = data_store.find_product("SKU-MEADOW-LF-2L")
    product["stock_by_store"]["Leeds Central"] = 0

    response = client.post(
        f"/api/orders/{ORDER_ID}/substitution-selection",
        json={
            "customer_id": CUSTOMER_ID,
            "original_sku_id": ORIGINAL_SKU,
            "selected_sku_id": "SKU-MEADOW-LF-2L",
            "notification_id": notification_id,
        },
    )
    assert response.status_code == 409


def test_no_eligible_alternative_creates_human_assistance_case(client):
    for sku in ("SKU-MEADOW-LF-2L", "SKU-MEADOW-LF-6X200ML"):
        product = data_store.find_product(sku)
        product["stock_by_store"]["Leeds Central"] = 0

    response = client.post("/api/events/short-pick", json=short_pick_payload())
    body = response.json()

    assert body["decision"]["outcome"] == "HUMAN_ASSISTANCE_REQUIRED"
    assert body["decision"]["valid_alternatives"] == []
    assert body["human_assistance"]["routing_reason"] == "GOLD_CUSTOMER_CRITICAL_PREFERENCE"
    option_types = {opt["type"] for opt in body["human_assistance"]["resolution_options"]}
    assert option_types == {"WALLET_CREDIT", "CARD_REFUND"}
    assert body["next_action"] == "WAIT_FOR_CUSTOMER_RESOLUTION_CHOICE"


def test_reset_endpoint_restores_original_demo_data(client):
    product = data_store.find_product("SKU-MEADOW-LF-2L")
    product["stock_by_store"]["Leeds Central"] = 0
    assert data_store.find_product("SKU-MEADOW-LF-2L")["stock_by_store"]["Leeds Central"] == 0

    response = client.post("/api/demo/reset")
    assert response.status_code == 200

    restored = data_store.find_product("SKU-MEADOW-LF-2L")
    assert restored["stock_by_store"]["Leeds Central"] == 7
