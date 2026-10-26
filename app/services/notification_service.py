from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.event import AlternativeOption, ResolutionOption
from app.repositories.json_repository import data_store
from app.services.audit_service import now_iso


def _new_status_history(status: str) -> List[Dict[str, str]]:
    return [{"status": status, "timestamp": now_iso()}]


def create_substitution_offer_notification(
    customer_id: str,
    order_id: str,
    original_product: Dict[str, Any],
    valid_alternatives: List[AlternativeOption],
) -> Dict[str, Any]:
    brand = original_product["brand"]
    count_text = "an option" if len(valid_alternatives) == 1 else f"{len(valid_alternatives)} options"
    message = (
        f"Your {original_product['product_name']} is out of stock today. "
        f"Here are {count_text} at the same price."
    )
    notification = {
        "notification_id": str(uuid.uuid4()),
        "notification_type": "SUBSTITUTION_OFFER",
        "channel": "whatsapp",
        "customer_id": customer_id,
        "order_id": order_id,
        "status": "SENT",
        "message": message,
        "alternatives": [alt.model_dump() for alt in valid_alternatives],
        "resolution_options": [],
        "responded_sku_id": None,
        "responded_resolution_choice": None,
        "status_history": [
            {"status": "READY_TO_SEND", "timestamp": now_iso()},
            {"status": "SENT", "timestamp": now_iso()},
        ],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with data_store.lock:
        data_store.notifications.data.append(notification)
    return notification


def create_human_assistance_notification(
    customer_id: str,
    order_id: str,
    original_product: Dict[str, Any],
    resolution_options: List[ResolutionOption],
) -> Dict[str, Any]:
    message = (
        f"We couldn't find a matching {original_product['brand']} alternative for "
        f"{original_product['product_name']} in stock today. Choose a wallet credit or "
        f"a refund, and one of our advisors will confirm with you."
    )
    notification = {
        "notification_id": str(uuid.uuid4()),
        "notification_type": "HUMAN_ASSISTANCE",
        "channel": "whatsapp",
        "customer_id": customer_id,
        "order_id": order_id,
        "status": "SENT",
        "message": message,
        "alternatives": [],
        "resolution_options": [opt.model_dump() for opt in resolution_options],
        "responded_sku_id": None,
        "responded_resolution_choice": None,
        "status_history": [
            {"status": "READY_TO_SEND", "timestamp": now_iso()},
            {"status": "SENT", "timestamp": now_iso()},
        ],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with data_store.lock:
        data_store.notifications.data.append(notification)
    return notification


def get_notification(notification_id: str) -> Dict[str, Any]:
    notification = data_store.find_notification(notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail=f"Notification '{notification_id}' not found")
    return notification


def mark_responded(
    notification_id: str,
    selected_sku_id: Optional[str] = None,
    resolution_choice: Optional[str] = None,
) -> Dict[str, Any]:
    with data_store.lock:
        notification = get_notification(notification_id)
        notification["status"] = "RESPONDED"
        notification["responded_sku_id"] = selected_sku_id
        notification["responded_resolution_choice"] = resolution_choice
        notification["status_history"].append({"status": "RESPONDED", "timestamp": now_iso()})
        notification["updated_at"] = now_iso()
    return notification
