from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import assistant_service, llm_service

router = APIRouter(prefix="/api/messages", tags=["messages"])


class GenerateMessageRequest(BaseModel):
    kind: str
    context: Dict[str, Any] = Field(default_factory=dict)
    fallback: str


class GenerateMessageResponse(BaseModel):
    message: str
    source: str


@router.post("/generate", response_model=GenerateMessageResponse)
def generate_message(request: GenerateMessageRequest) -> GenerateMessageResponse:
    message, source = llm_service.generate_message(
        kind=request.kind, context=request.context, fallback=request.fallback
    )
    return GenerateMessageResponse(message=message, source=source)


class AssistantChatRequest(BaseModel):
    customer_id: str
    order_id: Optional[str] = None
    message: str
    # Which point in the order's story this conversation represents - see
    # assistant_service.build_grounding_facts for what each value exposes.
    narrative_stage: assistant_service.NarrativeStage = "current"


class AssistantChatResponse(BaseModel):
    reply: str
    source: str


@router.post("/chat", response_model=AssistantChatResponse)
def assistant_chat(request: AssistantChatRequest) -> AssistantChatResponse:
    """Open-ended, memory-aware assistant chat - grounded in real backend data
    for this customer/order, with genuine multi-turn conversation history
    (unlike /generate, which is for one-off scripted/deterministic messages)."""
    grounding_facts = assistant_service.build_grounding_facts(
        request.customer_id, request.order_id, request.narrative_stage
    )
    history = assistant_service.get_session_history(request.customer_id)
    if not history:
        opening_context = assistant_service.describe_opening_context(request.narrative_stage, grounding_facts)
        if opening_context:
            grounding_facts["conversation_opening_context"] = opening_context
    session_messages = [*history, {"role": "user", "content": request.message}]

    reply, source = llm_service.generate_chat_reply(session_messages, grounding_facts)

    assistant_service.append_turn(request.customer_id, "user", request.message)
    assistant_service.append_turn(request.customer_id, "assistant", reply)

    return AssistantChatResponse(reply=reply, source=source)


class ClassifyFeedbackRequest(BaseModel):
    text: str
    history: List[Dict[str, str]] = Field(default_factory=list)


class ClassifyFeedbackResponse(BaseModel):
    area: Optional[Literal["products", "delivery", "both"]] = None
    sentiment: Literal["negative", "positive", "neutral"]
    source: str


@router.post("/classify-feedback", response_model=ClassifyFeedbackResponse)
def classify_feedback(request: ClassifyFeedbackRequest) -> ClassifyFeedbackResponse:
    """Backs the deterministic feedback-collection flow's area/sentiment
    routing with real language understanding instead of keyword matching -
    see llm_service.classify_feedback_signal."""
    result, source = llm_service.classify_feedback_signal(request.text, request.history)
    return ClassifyFeedbackResponse(area=result["area"], sentiment=result["sentiment"], source=source)
