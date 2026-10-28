from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.repositories.json_repository import data_store
from app.services import assistant_service, inventory_service

router = APIRouter(prefix="/api/demo", tags=["demo"])

_NO_MEADOW_SCENARIO_SKUS = ("SKU-MEADOW-LF-2L", "SKU-MEADOW-LF-6X200ML")
_SCENARIO_STORE_ID = "STORE-LEEDS-CENTRAL"


@router.post("/reset")
def reset_demo_data() -> Dict[str, Any]:
    data_store.reset_all()
    assistant_service.clear_all_sessions()
    return {"status": "RESET_COMPLETE", "message": "All mock data restored to its original state"}


@router.post("/scenarios/no-meadow")
def set_no_meadow_scenario() -> Dict[str, Any]:
    with data_store.lock:
        store_name = inventory_service.resolve_store_name(_SCENARIO_STORE_ID)
        products = [inventory_service.get_product_raw(sku_id) for sku_id in _NO_MEADOW_SCENARIO_SKUS]
        for product in products:
            product["stock_by_store"][store_name] = 0

    return {
        "status": "SCENARIO_READY",
        "scenario": "NO_MEADOW_AVAILABLE",
        "store_id": _SCENARIO_STORE_ID,
        "unavailable_skus": list(_NO_MEADOW_SCENARIO_SKUS),
    }
