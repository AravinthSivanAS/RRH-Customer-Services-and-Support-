from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("yacy.near.llm")

# Keyed by (kind, context, fallback): once a given demo step has produced a
# message, every later request for that exact step returns the same cached
# text instead of asking the LLM again. This keeps the wording for a given
# conversation step stable across repeated run-throughs of the demo (and
# avoids paying LLM latency twice for the same message).
_cache: Dict[str, str] = {}
_cache_lock = threading.Lock()


def _cache_key(kind: str, context: Dict[str, Any], fallback: str) -> str:
    return f"{kind}|{json.dumps(context, sort_keys=True, default=str)}|{fallback}"

_SYSTEM_PROMPT = (
    "You write short customer-facing WhatsApp messages for Yacy, an online grocery retailer. "
    "Tone: warm, concise, professional British English. Hard rules: "
    "1) Use only the facts given in the context - never invent prices, dates, names, brands, or products. "
    "2) When `conversation_history` is present in the context, read it first - it is everything said so far "
    "in this chat. Answer like a human who remembers the conversation: do not repeat an answer you already "
    "gave, do not ask a question that was already answered, and refer back to earlier turns where it helps. "
    "3) If the customer asks something context does not cover, say you don't have that detail - never "
    "deflect with a generic 'check your account/order history' line when the context actually contains the "
    "answer. "
    "4) Keep it to 1-3 sentences, no more than ~280 characters. "
    "5) No emoji unless the fallback example uses one. "
    "6) Reply with the message text only - no quotes, no preamble, no explanation."
)

# Per-kind nudges layered on top of the system prompt above. These steer *how*
# the model should reason about a given message kind without ever dictating
# the actual wording - the reply itself always comes from the LLM.
_KIND_GUIDANCE: Dict[str, str] = {
    # Which stage of the feedback conversation to move to next (sentiment, which
    # area the customer means, when to stop asking) is decided deterministically
    # in the frontend, not inferred by the LLM from raw history - that branching
    # needs to be reliable every time. Each kind below is a single, narrow
    # instruction for turning an already-decided step into natural wording.
    "FEEDBACK_ASK_AREA": (
        "The customer's feedback was negative. Acknowledge that briefly, then ask ONE question: did the "
        "issue concern the products, the delivery, or both? Do not name a specific product. Never use: "
        "sorry, apolog, on board, improve, remember."
    ),
    "FEEDBACK_ASK_WHICH_FIRST": (
        "The customer said both the products and the delivery had problems. Ask ONE short question: would "
        "they like to detail the products or the delivery first? Do not name a specific product."
    ),
    "FEEDBACK_ASK_AREA_DETAIL": (
        "`area` in context is either 'products' or 'delivery'. Ask ONE short question for more detail about "
        "that area specifically - for 'products': which item, or what was wrong with it, without naming a "
        "specific product yourself unless the customer already named one in `customer_feedback`; for "
        "'delivery': timing, the driver, or the condition it arrived in. Do not mention the other area at all."
    ),
    "FEEDBACK_ASK_NEXT_AREA_DETAIL": (
        "Acknowledge in a few words what the customer just told you about `acknowledged_area` (their words "
        "are in `customer_feedback`), then ask ONE short question for detail about `next_area` specifically - "
        "same rules as FEEDBACK_ASK_AREA_DETAIL (don't name a specific product unless the customer already "
        "has, and don't mention `acknowledged_area` again beyond the brief acknowledgement)."
    ),
    "FEEDBACK_FINAL_ACK": (
        "The customer has now given detail on every area that was a problem - `details` in context is an "
        "object keyed by area ('products' and/or 'delivery') with what they said about each. Write ONE warm, "
        "considerate closing message that specifically references what they told you for EACH area present "
        "in `details` (not a generic line), reassuring them it will be looked into for next time. No question "
        "mark. Never use: sorry, apolog, on board, remember, or the literal word 'improve'."
    ),
    "FEEDBACK_THANKS_POSITIVE": (
        "The customer's feedback was positive. Write a short, warm thank-you - if `customer_feedback` "
        "mentions something specific, thank them for that; otherwise thank them for the order/delivery as a "
        "whole. No question mark."
    ),
    "FEEDBACK_THANKS": (
        "This is a casual follow-up after the feedback conversation already wrapped up. Respond naturally "
        "and warmly to whatever the customer just said in `customer_feedback`, using the order facts in "
        "context if relevant. Keep it brief."
    ),
    "SUBSTITUTION_FOLLOWUP": (
        "Answer the customer's latest message directly using the order items, substitution, and delivery "
        "facts given in context. If they ask for a product list, name the actual products and quantities "
        "from context. Do not give a generic acknowledgement or ask them to contact us."
    ),
    "ORDER_CONFIRMATION_FOLLOWUP": (
        "Answer the customer's latest message directly using the order items and delivery facts given in "
        "context. If they ask for a product list, list the actual product names and quantities from context. "
        "Do not give a generic acknowledgement or ask them to contact us."
    ),
}

_FOLLOWUP_KINDS = {"ORDER_CONFIRMATION_FOLLOWUP", "SUBSTITUTION_FOLLOWUP"}

_client: Optional[object] = None
_client_checked = False


def _get_client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.info("OPENAI_API_KEY not set; LLM message generation disabled, using fallback text")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed; LLM message generation disabled")
        return None

    # OPENAI_BASE_URL lets this point at an Azure OpenAI resource's
    # SDK-compatible endpoint (e.g. https://<resource>.openai.azure.com/openai/v1)
    # instead of the public OpenAI API, using the same client class.
    base_url = os.environ.get("OPENAI_BASE_URL")

    try:
        _client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    except Exception:
        logger.exception("Failed to initialise OpenAI client; LLM message generation disabled")
        _client = None
    return _client


def generate_message(
    kind: str, context: Dict[str, Any], fallback: str
) -> Tuple[str, str]:
    """Generates customer-facing wording for a given message `kind` using the
    configured LLM. Always falls back to the provided deterministic text if
    no API key is configured, the `openai` package is missing, or the call
    fails for any reason - the demo must never break on a network/LLM issue.

    Returns (message_text, source) where source is "llm" or "fallback".
    """
    key = _cache_key(kind, context, fallback)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached, "llm-cached"

    client = _get_client()
    if client is None:
        return fallback, "fallback"

    default_model = "gpt-4o" if os.environ.get("OPENAI_BASE_URL") else "gpt-4o-mini"
    model = os.environ.get("OPENAI_MODEL", default_model)
    guidance = _KIND_GUIDANCE.get(kind)
    if kind in _FOLLOWUP_KINDS:
        example_prompt = (
            "\nWrite a direct answer to the customer's latest message. "
            "Do not use a generic acknowledgement."
        )
        guidance_prompt = f"\nInstructions for this kind: {guidance}" if guidance else ""
    else:
        example_prompt = (
            f"\nReference example, for length/format only - its tone may be wrong for this specific case, "
            f"follow the instructions above it instead: {fallback}"
        )
        guidance_prompt = (
            f"\nInstructions for this kind (these override the reference example's tone above): {guidance}"
            if guidance
            else ""
        )
    user_prompt = (
        f"Message kind: {kind}\n"
        f"Context facts (JSON): {context}"
        f"{example_prompt}{guidance_prompt}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=160,
            temperature=0.6,
            timeout=8,
        )
        text = (response.choices[0].message.content or "").strip()
        if text:
            with _cache_lock:
                _cache[key] = text
            return text, "llm"
        return fallback, "fallback"
    except Exception:
        logger.exception("LLM message generation failed for kind=%s; using fallback", kind)
        return fallback, "fallback"
