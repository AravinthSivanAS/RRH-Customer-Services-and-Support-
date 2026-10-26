from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from app.models.notification import SimulateResponseRequest
from app.repositories.json_repository import data_store
from app.services import notification_service, order_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/{notification_id}")
def get_notification(notification_id: str) -> Dict[str, Any]:
    return notification_service.get_notification(notification_id)


@router.post("/{notification_id}/simulate-response")
def simulate_response(notification_id: str, response: SimulateResponseRequest) -> Dict[str, Any]:
    notification = notification_service.get_notification(notification_id)
    order_id = notification["order_id"]
    case = data_store.substitution_cases.get(order_id)
    if case is None:
        raise HTTPException(
            status_code=404, detail=f"No substitution case found for order '{order_id}'"
        )

    if notification["notification_type"] == "SUBSTITUTION_OFFER":
        if not response.selected_sku_id:
            raise HTTPException(
                status_code=400,
                detail="selected_sku_id is required to respond to a substitution offer",
            )
        order_service.resolve_customer_selection(
            order_id=order_id,
            customer_id=notification["customer_id"],
            original_sku_id=case["original_sku_id"],
            selected_sku_id=response.selected_sku_id,
            notification_id=notification_id,
        )
    elif notification["notification_type"] == "HUMAN_ASSISTANCE":
        if not response.resolution_choice:
            raise HTTPException(
                status_code=400,
                detail="resolution_choice is required to respond to a human-assistance case",
            )
        order_service.resolve_human_assistance_choice(
            order_id=order_id,
            notification_id=notification_id,
            resolution_choice=response.resolution_choice,
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported notification type")

    return notification_service.get_notification(notification_id)
