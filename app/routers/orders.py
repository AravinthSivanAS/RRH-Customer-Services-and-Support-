from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.models.order import SubstitutionSelectionRequest
from app.repositories.json_repository import data_store
from app.services import audit_service, customer_service, llm_service, order_service

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _order_response(order: Dict[str, Any]) -> Dict[str, Any]:
    return {**order, "order_total": order_service.order_total(order)}


@router.get("/{order_id}")
def get_order(order_id: str) -> Dict[str, Any]:
    order = order_service.get_order_raw(order_id)
    return _order_response(order)


@router.get("/{order_id}/confirmation-message")
def get_confirmation_message(order_id: str) -> Dict[str, str]:
    """Order-confirmation WhatsApp message, framed entirely by the LLM from
    the real order record (orders.json) and customer record (customer_360.json)
    - the caller only supplies `order_id`, never pre-formatted date/time text,
    so the wording (including how the delivery date/slot reads) is genuinely
    the model's own phrasing of the real data, not a template filled in by
    the frontend."""
    order = order_service.get_order_raw(order_id)
    customer = customer_service.get_customer(order["customer_id"])
    fallback = (
        f"Hi {customer.customer_name} \U0001F44B Your order **{order['order_id']}** is confirmed for delivery "
        f"on **{order['delivery_date']}**, **{order['delivery_slot']}**. We'll message you here if anything "
        f"changes."
    )
    message, source = llm_service.generate_message(
        kind="ORDER_CONFIRMATION",
        context={
            "customer_name": customer.customer_name,
            "order_id": order["order_id"],
            "delivery_date": order["delivery_date"],
            "delivery_slot": order["delivery_slot"],
        },
        fallback=fallback,
    )
    return {"message": message, "source": source}


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
