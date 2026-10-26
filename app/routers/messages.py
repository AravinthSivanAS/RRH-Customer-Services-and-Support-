from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import llm_service

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
