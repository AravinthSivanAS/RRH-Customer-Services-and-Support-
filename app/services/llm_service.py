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
    "2) Keep it to 1-3 sentences, no more than ~280 characters. "
    "3) No emoji unless the fallback example uses one. "
    "4) Reply with the message text only - no quotes, no preamble, no explanation."
)

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
    user_prompt = (
        f"Message kind: {kind}\n"
        f"Context facts (JSON): {context}\n"
        f"Reference tone/length example (do not copy verbatim, write a fresh message "
        f"using only the context facts above): {fallback}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=120,
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
