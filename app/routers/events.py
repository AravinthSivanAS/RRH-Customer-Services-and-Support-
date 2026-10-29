from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from app.models.event import (
    ActorType,
    DecisionOutcome,
    EventType,
    HumanAssistanceCase,
    ResolutionOption,
    ShortPickEvent,
)
from app.repositories.json_repository import data_store
from app.services import (
    audit_service,
    customer_service,
    inventory_service,
    next_best_action_service,
    notification_service,
    order_service,
    preference_service,
)

router = APIRouter(prefix="/api/events", tags=["events"])


@router.post("/short-pick")
def short_pick(event: ShortPickEvent) -> Dict[str, Any]:
    if event.event_type != "SHORT_PICK_RECORDED":
        raise HTTPException(status_code=400, detail="event_type must be 'SHORT_PICK_RECORDED'")

    order = order_service.get_order_raw(event.order_id)
    order_service.ensure_order_is_editable(order)

    if order["customer_id"] != event.customer_id:
        raise HTTPException(
            status_code=400,
            detail=f"customer_id '{event.customer_id}' does not match order '{event.order_id}'",
        )
    if order["fulfilment_store_id"] != event.store_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"store_id '{event.store_id}' does not match order's fulfilment store "
                f"'{order['fulfilment_store_id']}'"
            ),
        )

    customer = customer_service.get_customer(event.customer_id)
    original_product = inventory_service.get_product_raw(event.sku_id)

    active_item = order_service.find_item(order, event.sku_id)
    if active_item is None:
        raise HTTPException(
            status_code=400,
            detail=f"Order '{event.order_id}' has no active line for '{event.sku_id}'",
        )

    dedup_key = f"{event.order_id}|{event.sku_id}"
    with data_store.lock:
        if dedup_key in data_store.processed_short_picks:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A short-pick for '{event.sku_id}' on order '{event.order_id}' "
                    f"has already been processed"
                ),
            )
        data_store.processed_short_picks.add(dedup_key)

    preference = preference_service.find_product_preference(customer, event.sku_id)
    rule_applied = preference.substitution_rule if preference else "NONE"

    valid_alternatives, rejected_alternatives = next_best_action_service.rank_alternatives(
        original_product, preference, event.store_id
    )

    active_item["item_status"] = "OUT_OF_STOCK"
    data_store.orders.save()

    event_record = audit_service.record_event(
        EventType.SHORT_PICK_RECORDED,
        order_id=event.order_id,
        customer_id=event.customer_id,
        store_id=event.store_id,
        sku_id=event.sku_id,
        payload=event.model_dump(),
    )

    human_assistance: HumanAssistanceCase | None = None

    if valid_alternatives:
        outcome = DecisionOutcome.CUSTOMER_SELECTION_REQUIRED
        notification = notification_service.create_substitution_offer_notification(
            customer_id=event.customer_id,
            order_id=event.order_id,
            original_product=original_product,
            valid_alternatives=valid_alternatives,
        )
        next_action = "WAIT_FOR_CUSTOMER_SELECTION"
    else:
        outcome = DecisionOutcome.HUMAN_ASSISTANCE_REQUIRED
        resolution_options = [
            ResolutionOption(
                type="WALLET_CREDIT",
                amount=original_product["unit_price"],
                currency=order["currency"],
                description="Instant wallet credit for the missing item",
            ),
            ResolutionOption(
                type="CARD_REFUND",
                amount=original_product["unit_price"],
                currency=order["currency"],
                description="Refund to the original payment card, pending settlement",
            ),
        ]
        routing_reason = (
            "GOLD_CUSTOMER_CRITICAL_PREFERENCE"
            if customer.loyalty_tier == "Gold" and preference is not None
            else "NO_ELIGIBLE_ALTERNATIVE"
        )
        human_assistance = HumanAssistanceCase(
            routing_reason=routing_reason,
            assigned_queue=(
                "GOLD_PRIORITY" if routing_reason == "GOLD_CUSTOMER_CRITICAL_PREFERENCE" else "STANDARD"
            ),
            resolution_options=resolution_options,
        )
        notification = notification_service.create_human_assistance_notification(
            customer_id=event.customer_id,
            order_id=event.order_id,
            original_product=original_product,
            resolution_options=resolution_options,
        )
        next_action = "WAIT_FOR_CUSTOMER_RESOLUTION_CHOICE"

    case: Dict[str, Any] = {
        "order_id": event.order_id,
        "customer_id": event.customer_id,
        "original_sku_id": event.sku_id,
        "store_id": event.store_id,
        "requested_quantity": event.requested_quantity,
        "outcome": outcome.value,
        "rule_applied": rule_applied,
        "valid_alternatives": [alt.model_dump() for alt in valid_alternatives],
        "rejected_alternatives": [r.model_dump() for r in rejected_alternatives],
        "notification_id": notification["notification_id"],
        "human_assistance": human_assistance.model_dump() if human_assistance else None,
        "resolved": False,
        "created_at": audit_service.now_iso(),
    }
    with data_store.lock:
        data_store.substitution_cases[event.order_id] = case

    audit_service.record_audit(
        event_record,
        decision_outcome=outcome,
        actor_type=ActorType.SYSTEM,
        original_sku=event.sku_id,
        rule_applied=rule_applied,
        rejection_reasons=[r.reason for r in rejected_alternatives],
        notification_channel="whatsapp",
    )

    return {
        "event_id": event_record["event_id"],
        "order_id": event.order_id,
        "customer_id": event.customer_id,
        "original_product": {
            "sku_id": original_product["sku_id"],
            "product_name": original_product["product_name"],
            "status": "OUT_OF_STOCK",
        },
        "customer_rule": {
            "rule": rule_applied,
            "preferred_brand": preference.preferred_brand if preference else None,
        },
        "decision": {
            "outcome": outcome.value,
            "valid_alternatives": case["valid_alternatives"],
            "rejected_alternatives": case["rejected_alternatives"],
        },
        "notification": {
            "notification_id": notification["notification_id"],
            "channel": notification["channel"],
            "status": notification["status"],
        },
        "human_assistance": case["human_assistance"],
        "next_action": next_action,
    }
