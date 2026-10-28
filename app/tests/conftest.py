from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.json_repository import data_store
from app.services import assistant_service, llm_service

ORDER_ID = "ORD-2026-10001"
CUSTOMER_ID = "CUST-1001"
STORE_ID = "STORE-LEEDS-CENTRAL"
ORIGINAL_SKU = "SKU-MEADOW-LF-1L"


@pytest.fixture(autouse=True)
def reset_data_store():
    data_store.reset_all()
    assistant_service.clear_all_sessions()
    yield
    data_store.reset_all()
    assistant_service.clear_all_sessions()


@pytest.fixture(autouse=True)
def no_live_llm_calls(monkeypatch):
    """Tests must stay fast, free, and offline: never hit a real LLM provider,
    regardless of what's configured in .env. Always exercise the deterministic
    fallback path that ships with every message kind."""

    def _fallback_only(kind, context, fallback):
        return fallback, "fallback"

    def _chat_fallback_only(session_messages, grounding_facts):
        return "This is a test fallback reply.", "fallback"

    def _classify_fallback_only(text, history):
        return {"area": None, "sentiment": "neutral"}, "fallback"

    monkeypatch.setattr(llm_service, "generate_message", _fallback_only)
    monkeypatch.setattr(llm_service, "generate_chat_reply", _chat_fallback_only)
    monkeypatch.setattr(llm_service, "classify_feedback_signal", _classify_fallback_only)


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
