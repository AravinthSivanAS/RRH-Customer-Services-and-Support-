from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app.models.event import ActorType, DecisionOutcome, EventType
from app.models.order import Order
from app.repositories.json_repository import data_store
from app.services import audit_service, inventory_service, notification_service

_LOCKED_STATUSES = {"DISPATCHED", "DELIVERED", "CANCELLED"}


def get_order(order_id: str) -> Order:
    raw = get_order_raw(order_id)
    order = dict(raw)
    return Order.model_validate(order)


def get_order_raw(order_id: str) -> Dict[str, Any]:
    raw = data_store.find_order(order_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")
    return raw


def ensure_order_is_editable(order: Dict[str, Any]) -> None:
    if order["status"] in _LOCKED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Order '{order['order_id']}' is already {order['status']} and cannot be updated",
        )


def find_item(order: Dict[str, Any], sku_id: str) -> Dict[str, Any] | None:
    for item in order["items"]:
        if item["sku_id"] == sku_id and item["item_status"] not in ("SUBSTITUTED",):
            return item
    return None


def apply_substitution(
    order: Dict[str, Any],
    original_sku_id: str,
    selected_product: Dict[str, Any],
    quantity: int,
    reason: str,
    decision_source: str,
) -> None:
    with data_store.lock:
        original_item = find_item(order, original_sku_id)
        if original_item is None:
            raise HTTPException(
                status_code=400,
                detail=f"Order '{order['order_id']}' has no active line for '{original_sku_id}'",
            )
        original_item["item_status"] = "SUBSTITUTED"
        original_item["substitution_reason"] = reason
        original_item["decision_source"] = decision_source

        order["items"].append(
            {
                "sku_id": selected_product["sku_id"],
                "product_name": selected_product["product_name"],
                "quantity": quantity,
                "unit_price": selected_product["unit_price"],
                "item_status": "APPROVED_SUBSTITUTION",
                "substitution_reason": reason,
                "decision_source": decision_source,
                "original_sku_id": original_sku_id,
            }
        )


def order_total(order: Dict[str, Any]) -> float:
    return round(
        sum(
            item["unit_price"] * item["quantity"]
            for item in order["items"]
            if item["item_status"] != "SUBSTITUTED"
        ),
        2,
    )


def resolve_customer_selection(
    order_id: str,
    customer_id: str,
    original_sku_id: str,
    selected_sku_id: str,
    notification_id: str,
    actor_type: ActorType = ActorType.CUSTOMER,
) -> Dict[str, Any]:
    with data_store.lock:
        order = get_order_raw(order_id)
        ensure_order_is_editable(order)

        if order["customer_id"] != customer_id:
            raise HTTPException(
                status_code=400,
                detail=f"Customer '{customer_id}' does not match order '{order_id}'",
            )

        case = data_store.substitution_cases.get(order_id)
        if case is None or case["original_sku_id"] != original_sku_id:
            raise HTTPException(
                status_code=404,
                detail=f"No open substitution case for order '{order_id}' and sku '{original_sku_id}'",
            )
        if case["resolved"]:
            raise HTTPException(
                status_code=409,
                detail="This substitution has already been applied",
            )
        if case["notification_id"] != notification_id:
            raise HTTPException(
                status_code=400,
                detail="Notification does not match the open substitution case for this order",
            )

        offered = {alt["sku_id"]: alt for alt in case["valid_alternatives"]}
        if selected_sku_id not in offered:
            raise HTTPException(
                status_code=400,
                detail=f"'{selected_sku_id}' was not offered as an alternative for this order",
            )

        quantity = case["requested_quantity"]
        store_id = case["store_id"]

        # Re-check stock to guard against stale inventory between offer and selection.
        inventory_service.decrement_stock(selected_sku_id, store_id, quantity)
        selected_product = inventory_service.get_product_raw(selected_sku_id)

        reason = (
            f"Customer selected a same-brand alternative under the "
            f"{case['rule_applied']} preference"
        )
        apply_substitution(
            order, original_sku_id, selected_product, quantity, reason, actor_type.value
        )
        case["resolved"] = True

        notification_service.mark_responded(notification_id, selected_sku_id=selected_sku_id)

        event = audit_service.record_event(
            EventType.SUBSTITUTION_APPLIED,
            order_id=order_id,
            customer_id=customer_id,
            store_id=store_id,
            sku_id=selected_sku_id,
            payload={"original_sku_id": original_sku_id, "selected_sku_id": selected_sku_id},
        )
        audit_service.record_audit(
            event,
            decision_outcome=DecisionOutcome.SUBSTITUTION_APPLIED,
            actor_type=actor_type,
            original_sku=original_sku_id,
            selected_sku=selected_sku_id,
            rule_applied=case["rule_applied"],
            notification_channel="whatsapp",
        )

        return order


def resolve_human_assistance_choice(
    order_id: str,
    notification_id: str,
    resolution_choice: str,
    actor_type: ActorType = ActorType.CUSTOMER,
) -> Dict[str, Any]:
    with data_store.lock:
        order = get_order_raw(order_id)
        ensure_order_is_editable(order)

        case = data_store.substitution_cases.get(order_id)
        if case is None or case.get("notification_id") != notification_id:
            raise HTTPException(
                status_code=404,
                detail=f"No open human-assistance case for order '{order_id}'",
            )
        if case["resolved"]:
            raise HTTPException(status_code=409, detail="This case has already been resolved")

        valid_choices = {opt["type"] for opt in case["human_assistance"]["resolution_options"]}
        if resolution_choice not in valid_choices:
            raise HTTPException(
                status_code=400,
                detail=f"'{resolution_choice}' is not one of the offered resolution options",
            )

        case["resolved"] = True
        notification_service.mark_responded(notification_id, resolution_choice=resolution_choice)

        event = audit_service.record_event(
            EventType.NOTIFICATION_RESPONDED,
            order_id=order_id,
            customer_id=order["customer_id"],
            store_id=case["store_id"],
            sku_id=case["original_sku_id"],
            payload={"resolution_choice": resolution_choice},
        )
        audit_service.record_audit(
            event,
            decision_outcome=DecisionOutcome.RESOLVED_BY_HUMAN,
            actor_type=actor_type,
            original_sku=case["original_sku_id"],
            rule_applied=case["rule_applied"],
            notification_channel="whatsapp",
        )

        return order
