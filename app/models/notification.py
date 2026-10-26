from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.event import AlternativeOption, ResolutionOption


class NotificationChannel(str, Enum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    SMS = "sms"


class NotificationStatus(str, Enum):
    READY_TO_SEND = "READY_TO_SEND"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    RESPONDED = "RESPONDED"


class NotificationType(str, Enum):
    SUBSTITUTION_OFFER = "SUBSTITUTION_OFFER"
    HUMAN_ASSISTANCE = "HUMAN_ASSISTANCE"


class StatusHistoryEntry(BaseModel):
    status: NotificationStatus
    timestamp: str


class Notification(BaseModel):
    notification_id: str
    notification_type: NotificationType
    channel: NotificationChannel
    customer_id: str
    order_id: str
    status: NotificationStatus
    message: str
    alternatives: List[AlternativeOption] = Field(default_factory=list)
    resolution_options: List[ResolutionOption] = Field(default_factory=list)
    responded_sku_id: Optional[str] = None
    responded_resolution_choice: Optional[str] = None
    status_history: List[StatusHistoryEntry] = Field(default_factory=list)
    created_at: str
    updated_at: str


class SimulateResponseRequest(BaseModel):
    selected_sku_id: Optional[str] = None
    resolution_choice: Optional[str] = None
