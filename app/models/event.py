from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ActorType(str, Enum):
    SYSTEM = "SYSTEM"
    CUSTOMER = "CUSTOMER"
    ADVISOR = "ADVISOR"


class EventType(str, Enum):
    SHORT_PICK_RECORDED = "SHORT_PICK_RECORDED"
    SUBSTITUTION_OFFERED = "SUBSTITUTION_OFFERED"
    SUBSTITUTION_SELECTED = "SUBSTITUTION_SELECTED"
    SUBSTITUTION_APPLIED = "SUBSTITUTION_APPLIED"
    HUMAN_ASSISTANCE_ROUTED = "HUMAN_ASSISTANCE_ROUTED"
    PREFERENCE_UPDATED = "PREFERENCE_UPDATED"
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    NOTIFICATION_RESPONDED = "NOTIFICATION_RESPONDED"
    DEMO_RESET = "DEMO_RESET"


class DecisionOutcome(str, Enum):
    CUSTOMER_SELECTION_REQUIRED = "CUSTOMER_SELECTION_REQUIRED"
    HUMAN_ASSISTANCE_REQUIRED = "HUMAN_ASSISTANCE_REQUIRED"
    SUBSTITUTION_APPLIED = "SUBSTITUTION_APPLIED"
    PREFERENCE_UPDATED = "PREFERENCE_UPDATED"
    RESOLVED_BY_HUMAN = "RESOLVED_BY_HUMAN"


class ShortPickEvent(BaseModel):
    event_type: str = Field(default="SHORT_PICK_RECORDED")
    event_time: str
    order_id: str
    customer_id: str
    store_id: str
    sku_id: str
    requested_quantity: int = Field(gt=0)
    available_quantity: int = Field(ge=0)
    picker_id: str


class AlternativeOption(BaseModel):
    sku_id: str
    product_name: str
    brand: str
    unit_price: float
    available_quantity: int
    score: int
    reasons: List[str]


class RejectedAlternative(BaseModel):
    sku_id: str
    product_name: str
    brand: str
    reason: str


class ResolutionOption(BaseModel):
    type: str
    amount: float
    currency: str
    description: str


class HumanAssistanceCase(BaseModel):
    routing_reason: str
    assigned_queue: str
    resolution_options: List[ResolutionOption]


class Event(BaseModel):
    event_id: str
    event_type: EventType
    event_time: str
    order_id: str
    customer_id: str
    store_id: Optional[str] = None
    sku_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)


class AuditEntry(BaseModel):
    audit_id: str
    event_id: str
    event_type: EventType
    timestamp: str
    customer_id: str
    order_id: str
    original_sku: Optional[str] = None
    selected_sku: Optional[str] = None
    rule_applied: Optional[str] = None
    decision_outcome: DecisionOutcome
    rejection_reasons: List[str] = Field(default_factory=list)
    notification_channel: Optional[str] = None
    actor_type: ActorType


class SubstitutionCase(BaseModel):
    order_id: str
    customer_id: str
    original_sku_id: str
    store_id: str
    outcome: DecisionOutcome
    valid_alternatives: List[AlternativeOption] = Field(default_factory=list)
    rejected_alternatives: List[RejectedAlternative] = Field(default_factory=list)
    notification_id: Optional[str] = None
    human_assistance: Optional[HumanAssistanceCase] = None
    resolved: bool = False
    created_at: str = ""
