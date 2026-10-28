from __future__ import annotations

import threading
from typing import Any, Dict, List, Literal, Optional

from app.repositories.json_repository import data_store
from app.services import customer_service, order_service

# Per-customer chat history for the open-ended assistant, kept in memory for
# the lifetime of the process - same lightweight approach as the rest of this
# demo's mock data (see app.repositories.json_repository.DataStore). Keyed by
# customer_id so different customers' conversations never mix, even though
# this demo only ever drives one customer at a time.
_sessions: Dict[str, List[Dict[str, str]]] = {}
_sessions_lock = threading.Lock()

_MAX_TURNS_KEPT = 20

NarrativeStage = Literal["order_placed", "current"]


def get_session_history(customer_id: str) -> List[Dict[str, str]]:
    with _sessions_lock:
        return list(_sessions.get(customer_id, []))


def append_turn(customer_id: str, role: str, content: str) -> None:
    with _sessions_lock:
        history = _sessions.setdefault(customer_id, [])
        history.append({"role": role, "content": content})
        if len(history) > _MAX_TURNS_KEPT:
            del history[: len(history) - _MAX_TURNS_KEPT]


def clear_session(customer_id: str) -> None:
    with _sessions_lock:
        _sessions.pop(customer_id, None)


def clear_all_sessions() -> None:
    with _sessions_lock:
        _sessions.clear()


def build_grounding_facts(
    customer_id: str, order_id: Optional[str], narrative_stage: NarrativeStage = "current"
) -> Dict[str, Any]:
    """Real facts the assistant is allowed to state as true, sourced from the
    same services the REST API uses - never invented, never guessed from
    which screen the customer happens to be looking at.

    `narrative_stage` controls how much of the order's live state is exposed:
    - "order_placed": for conversations that, in the demo's story, happen
      right when the order was placed - before any out-of-stock/substitution
      event has occurred in that part of the story. Even if the backend's
      mock order has since moved on (e.g. the customer already completed a
      later part of the flow and navigated back), this view reconstructs the
      order as originally placed so the assistant can't "see into the
      future" and mention an out-of-stock substitution before it has
      happened in that conversation's own narrative.
    - "current": the full live state, including any substitution that has
      taken place and exactly what was offered/chosen - used once the
      out-of-stock conversation has actually started.
    """
    facts: Dict[str, Any] = {}

    try:
        customer = customer_service.get_customer(customer_id)
        facts["customer_name"] = customer.customer_name
    except Exception:
        facts["customer_name"] = None
        facts["customer_lookup_error"] = f"No customer record found for '{customer_id}'"

    if not order_id:
        facts["order"] = None
        facts["order_lookup_note"] = "No order_id was provided for this conversation"
        return facts

    try:
        order = order_service.get_order(order_id)
    except Exception:
        facts["order"] = None
        facts["order_lookup_error"] = f"No order found for '{order_id}'"
        return facts

    if narrative_stage == "order_placed":
        facts["order"] = {
            "order_id": order.order_id,
            "delivery_date": order.delivery_date,
            "delivery_slot": order.delivery_slot,
            "currency": order.currency,
            # Reconstructed as originally ordered: the line added for a later
            # substitution is dropped, and a substituted-away line is shown
            # under its original name/status - this conversation's narrative
            # is still "order just placed", nothing has gone out of stock yet.
            "items": [
                {"product_name": item.product_name, "quantity": item.quantity, "unit_price": item.unit_price, "status": "ordered"}
                for item in order.items
                if item.original_sku_id is None
            ],
        }
        return facts

    case = data_store.substitution_cases.get(order_id)
    substitution_case = None
    if case:
        chosen_product = None
        if case.get("resolved"):
            chosen_sku = next(
                (item.sku_id for item in order.items if item.original_sku_id == case.get("original_sku_id")),
                None,
            )
            chosen_product = next((item.product_name for item in order.items if item.sku_id == chosen_sku), None)
        substitution_case = {
            "alternatives_offered": [
                {"product_name": alt["product_name"], "brand": alt["brand"], "unit_price": alt["unit_price"]}
                for alt in case.get("valid_alternatives", [])
            ],
            "resolved": case.get("resolved", False),
            "chosen_product": chosen_product,
            "note": (
                "These were the ONLY alternatives available at the time. If resolved, the customer's choice is "
                "already applied to the order - there is no further 'request another alternative' or 'contact "
                "the team' process to offer; if they ask for something else, explain plainly that only these "
                "options were available."
            ),
        }

    facts["order"] = {
        "order_id": order.order_id,
        "status": order.status.value,
        "delivery_date": order.delivery_date,
        "delivery_slot": order.delivery_slot,
        "order_total": order.order_total,
        "currency": order.currency,
        "items": [
            {
                "product_name": item.product_name,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "status": item.item_status.value,
                "substituted_for_sku": item.original_sku_id,
                "substitution_reason": item.substitution_reason,
            }
            for item in order.items
        ],
    }
    if substitution_case:
        facts["substitution_case"] = substitution_case
    return facts


def describe_opening_context(narrative_stage: NarrativeStage, facts: Dict[str, Any]) -> Optional[str]:
    """A one-line description of what just happened right before this chat
    session started, for when the customer's very first message has no prior
    turns to react to (e.g. a bare "ok"/"fine"/"thanks"). Without this, the
    assistant has no idea what such a short reply is even responding to and
    tends to fall back on a generic "how can I help?" opener - this fills in
    that missing context without inventing a fake conversation turn."""
    order = facts.get("order")
    if not order:
        return None

    if narrative_stage == "order_placed":
        return (
            f"This chat just started, right after the customer received their order confirmation for "
            f"order {order.get('order_id')}, confirming delivery on {order.get('delivery_date')} between "
            f"{order.get('delivery_slot')}. If their first message is just a short acknowledgement (e.g. "
            f"'ok', 'fine', 'thanks', 'great'), respond to that specifically - e.g. confirm you'll keep them "
            f"posted - rather than with a generic opening question."
        )

    substitution_case = facts.get("substitution_case")
    if substitution_case and substitution_case.get("resolved"):
        chosen = substitution_case.get("chosen_product")
        return (
            f"This chat just started, right after the customer chose '{chosen}' as their substitution and it "
            f"was applied to order {order.get('order_id')}. If their first message is just a short "
            f"acknowledgement (e.g. 'ok', 'fine', 'thanks'), respond to that specifically rather than with a "
            f"generic opening question."
        )

    return None
