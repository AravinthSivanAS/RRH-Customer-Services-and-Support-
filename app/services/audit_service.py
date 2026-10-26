from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.event import ActorType, DecisionOutcome, EventType
from app.repositories.json_repository import data_store


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_event(
    event_type: EventType,
    order_id: str,
    customer_id: str,
    store_id: Optional[str] = None,
    sku_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type.value,
        "event_time": now_iso(),
        "order_id": order_id,
        "customer_id": customer_id,
        "store_id": store_id,
        "sku_id": sku_id,
        "payload": payload or {},
    }
    with data_store.lock:
        data_store.events.data.append(event)
    return event


def record_audit(
    event: Dict[str, Any],
    decision_outcome: DecisionOutcome,
    actor_type: ActorType,
    original_sku: Optional[str] = None,
    selected_sku: Optional[str] = None,
    rule_applied: Optional[str] = None,
    rejection_reasons: Optional[List[str]] = None,
    notification_channel: Optional[str] = None,
) -> Dict[str, Any]:
    audit_entry = {
        "audit_id": str(uuid.uuid4()),
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "timestamp": now_iso(),
        "customer_id": event["customer_id"],
        "order_id": event["order_id"],
        "original_sku": original_sku,
        "selected_sku": selected_sku,
        "rule_applied": rule_applied,
        "decision_outcome": decision_outcome.value,
        "rejection_reasons": rejection_reasons or [],
        "notification_channel": notification_channel,
        "actor_type": actor_type.value,
    }
    with data_store.lock:
        event.setdefault("audit_entries", [])
        event["audit_entries"].append(audit_entry)
    return audit_entry


def get_timeline(order_id: str) -> List[Dict[str, Any]]:
    order = data_store.find_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")

    timeline: List[Dict[str, Any]] = []
    for event in data_store.events_for_order(order_id):
        timeline.append(
            {
                "kind": "EVENT",
                "timestamp": event["event_time"],
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "sku_id": event.get("sku_id"),
                "store_id": event.get("store_id"),
                "payload": event.get("payload", {}),
            }
        )
        for audit_entry in event.get("audit_entries", []):
            timeline.append(
                {
                    "kind": "AUDIT",
                    "timestamp": audit_entry["timestamp"],
                    **audit_entry,
                }
            )

    for notification in data_store.notifications.data:
        if notification["order_id"] != order_id:
            continue
        timeline.append(
            {
                "kind": "NOTIFICATION",
                "timestamp": notification["created_at"],
                "notification_id": notification["notification_id"],
                "channel": notification["channel"],
                "status": notification["status"],
                "message": notification["message"],
            }
        )

    timeline.sort(key=lambda item: item["timestamp"])
    return timeline
