from __future__ import annotations

from fastapi import APIRouter

from app.models.customer import Customer360, CustomerPreferences, ProductPreferenceUpdateRequest
from app.models.event import ActorType, DecisionOutcome, EventType
from app.services import audit_service, customer_service

router = APIRouter(prefix="/api/customers", tags=["customers"])


@router.get("/{customer_id}", response_model=Customer360)
def get_customer(customer_id: str) -> Customer360:
    return customer_service.get_customer(customer_id)


@router.get("/{customer_id}/preferences", response_model=CustomerPreferences)
def get_customer_preferences(customer_id: str) -> CustomerPreferences:
    return customer_service.get_preferences(customer_id)


@router.post("/{customer_id}/preferences", response_model=CustomerPreferences)
def save_customer_preference(
    customer_id: str, update: ProductPreferenceUpdateRequest
) -> CustomerPreferences:
    customer_service.get_customer(customer_id)  # 404 if missing
    preferences = customer_service.upsert_product_preference(customer_id, update)

    event = audit_service.record_event(
        EventType.PREFERENCE_UPDATED,
        order_id="N/A",
        customer_id=customer_id,
        sku_id=update.preferred_sku_id,
        payload=update.model_dump(mode="json"),
    )
    audit_service.record_audit(
        event,
        decision_outcome=DecisionOutcome.PREFERENCE_UPDATED,
        actor_type=ActorType.CUSTOMER,
        original_sku=update.preferred_sku_id,
        rule_applied=update.substitution_rule.value,
    )

    return preferences
