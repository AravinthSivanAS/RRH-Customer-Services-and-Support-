from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.json_repository import data_store

ORDER_ID = "ORD-2026-10001"
CUSTOMER_ID = "CUST-1001"
STORE_ID = "STORE-LEEDS-CENTRAL"
ORIGINAL_SKU = "SKU-MEADOW-LF-1L"


@pytest.fixture(autouse=True)
def reset_data_store():
    data_store.reset_all()
    yield
    data_store.reset_all()


@pytest.fixture
def client():
    return TestClient(app)


def short_pick_payload(**overrides):
    payload = {
        "event_type": "SHORT_PICK_RECORDED",
        "event_time": "2026-10-27T06:30:00Z",
        "order_id": ORDER_ID,
        "customer_id": CUSTOMER_ID,
        "store_id": STORE_ID,
        "sku_id": ORIGINAL_SKU,
        "requested_quantity": 1,
        "available_quantity": 0,
        "picker_id": "PICKER-204",
    }
    payload.update(overrides)
    return payload
