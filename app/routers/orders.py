from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.models.order import SubstitutionSelectionRequest
from app.repositories.json_repository import data_store
from app.services import audit_service, order_service

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _order_response(order: Dict[str, Any]) -> Dict[str, Any]:
    return {**order, "order_total": order_service.order_total(order)}


@router.get("/{order_id}")
def get_order(order_id: str) -> Dict[str, Any]:
    order = order_service.get_order_raw(order_id)
    return _order_response(order)


@router.get("/{order_id}/substitution-options")
def get_substitution_options(order_id: str) -> Dict[str, Any]:
    order_service.get_order_raw(order_id)  # 404 if the order itself is missing
    case = data_store.substitution_cases.get(order_id)
    if case is None:
        raise HTTPException(
            status_code=404, detail=f"No substitution case has been raised for order '{order_id}'"
        )
    return case


@router.post("/{order_id}/substitution-selection")
def select_substitution(order_id: str, selection: SubstitutionSelectionRequest) -> Dict[str, Any]:
    order = order_service.resolve_customer_selection(
        order_id=order_id,
        customer_id=selection.customer_id,
        original_sku_id=selection.original_sku_id,
        selected_sku_id=selection.selected_sku_id,
        notification_id=selection.notification_id,
    )
    return _order_response(order)


@router.get("/{order_id}/timeline")
def get_timeline(order_id: str) -> List[Dict[str, Any]]:
    return audit_service.get_timeline(order_id)
