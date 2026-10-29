from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

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
    "6) Reply with the message text only - no quotes, no preamble, no explanation. "
    "7) If the customer describes something that contradicts a fact given in context (e.g. they say the order "
    "was never delivered but `delivery_status` is 'delivered', or they blame an item that isn't actually "
    "missing), do not apologise for or confirm that specific claim as true - state the real fact plainly, "
    "while still being empathetic about whatever genuinely was an issue."
)

# Per-kind nudges layered on top of the system prompt above. These steer *how*
# the model should reason about a given message kind without ever dictating
# the actual wording - the reply itself always comes from the LLM.
_KIND_GUIDANCE: Dict[str, str] = {
    "SUBSTITUTION_OFFER": (
        "Tell the customer `original_product_name` is out of stock today, then mention the alternatives in "
        "`alternatives` (each has product_name, brand, unit_price) and ask which works best for them. Wrap "
        "the out-of-stock product name, and the brand name every time it's mentioned, in double asterisks for "
        "emphasis (e.g. '**Meadow Lactose-Free Milk 1L**', '**Meadow**') - these are the facts the customer "
        "should notice at a glance; the per-option price/pack details are shown separately below this message "
        "so don't repeat every alternative's exact spec here. Keep it brief and warm."
    ),
    "ORDER_CONFIRMATION": (
        "Confirm the order for `customer_name` by name, and state the order id `order_id`. `delivery_date` is "
        "a raw ISO date (e.g. '2026-10-27') and `delivery_slot` is a raw 24-hour time range (e.g. "
        "'18:00-20:00') - phrase both naturally for a customer message (e.g. 'Tuesday 27 October', 'between "
        "18:00 and 20:00') without changing the actual date or times they represent. Wrap the order id, the "
        "phrased delivery date, and the phrased delivery time slot each in double asterisks for emphasis (e.g. "
        "'**ORD-2026-10001**', '**Tuesday 27 October**', '**between 18:00 and 20:00**') - nothing else in the "
        "message should be bolded. End with a short, warm reassurance that we'll message them if anything "
        "changes. One or two sentences."
    ),
    "DELIVERED_WITH_SWAP": (
        "Tell the customer their order has been delivered, including the swap they approved - "
        "`swapped_product_name` is the item that was substituted in. Wrap that product name in double asterisks "
        "for emphasis. Then ask ONE short question in the same message: was today's swap right for them? Keep "
        "it brief and warm, no more than two sentences total."
    ),
    "ASSISTANT_SWAP_PREFERENCE_REPLY": (
        "The customer just typed `customer_message`, about whether `swapped_product_name` should be remembered "
        "as their go-to whenever `original_product_name` is out of stock. `preference_saved` in context is the "
        "TRUTH of what actually happened just now - true only if it was genuinely saved, false otherwise (they "
        "declined, or the message wasn't a clear yes). Reply naturally to what they actually typed, and be "
        "truthful to `preference_saved` - never say it's saved unless it's true. If true: confirm warmly it's "
        "saved and will apply automatically next time, wrapping `swapped_product_name` in double asterisks, no "
        "question needed. If false because they declined: acknowledge warmly that you won't set a default, "
        "without claiming anything was saved. If false because the message was unrelated or ambiguous: respond "
        "naturally to what they said and, if it makes sense, ask plainly whether they'd like it remembered. One "
        "or two sentences."
    ),
    "ASSISTANT_FOLLOWUP_CHAT": (
        "`swapped_product_name` has ALREADY been saved as the customer's go-to for whenever "
        "`original_product_name` is out of stock, earlier in this same conversation - do not say it's being "
        "saved, confirm it again, or repeat that confirmation. The customer just typed `customer_message` as a "
        "casual follow-up (e.g. acknowledging, thanking you, saying ok/cool, or asking something new). Respond "
        "naturally and warmly to what they actually said, and offer to help with anything else. One short "
        "sentence, two at most."
    ),
    # Which stage of the feedback conversation to move to next (sentiment, which
    # area the customer means, when to stop asking) is decided deterministically
    # in the frontend, not inferred by the LLM from raw history - that branching
    # needs to be reliable every time. Each kind below is a single, narrow
    # instruction for turning an already-decided step into natural wording.
    "FEEDBACK_ASK_CLARIFY": (
        "The customer's last reply was ambiguous - it wasn't clearly a complaint or clearly fine. Do not guess "
        "either way. Gently ask them to clarify, phrased close to the reference example's question, in a warm, "
        "brief way. Do not apologise (we don't yet know there's anything to apologise for)."
    ),
    "FEEDBACK_ASK_AREA": (
        "The customer's feedback was negative - read how strongly negative `customer_feedback` sounds. If it "
        "sounds like something actually went wrong (not just lukewarm), open with a short, genuine apology "
        "that fits what they said, not a generic 'sorry for any inconvenience' stock phrase. Then ask ONE "
        "question: did the issue concern the products, the delivery, or both? Do not name a specific product."
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
        "are in `customer_feedback`) - but first check it against the real order facts also in context "
        "(`delivery_status`, `items`, `resolution_choice`, `substitution_chosen`): if what they said "
        "contradicts those facts (e.g. they claim it wasn't delivered but `delivery_status` is 'delivered'), "
        "do not apologise for or confirm that specific claim - acknowledge briefly while stating the real "
        "fact instead. Otherwise, if what they described sounds genuinely bad (damaged, wrong, rotten, very "
        "late, etc.) and matches the facts, that acknowledgement should include a short, sincere apology "
        "naming the specific thing, not a generic one. Then ask ONE short question for detail about "
        "`next_area` specifically - same rules as FEEDBACK_ASK_AREA_DETAIL (don't name a specific product "
        "unless the customer already has, and don't dwell on `acknowledged_area` beyond that one sentence)."
    ),
    "FEEDBACK_FINAL_ACK": (
        "The customer has now given detail on every area that was a problem - `details` in context is an "
        "object keyed by area ('products' and/or 'delivery') with what they said about each. First check each "
        "one against the real order facts also in context (`delivery_status`, `items`, `resolution_choice`, "
        "`substitution_chosen`): if what the customer described for an area contradicts those facts (e.g. "
        "they say it was never delivered but `delivery_status` is 'delivered', or they blame an item that "
        "isn't actually missing per `items`), do not apologise for or confirm that specific claim - state the "
        "real fact plainly for that area instead. For anything that does match the facts, judge how serious "
        "it is (e.g. rotten/damaged food, a late or missing delivery is serious; a minor gripe is not). Write "
        "ONE closing message that specifically references what they told you for EACH area present in "
        "`details` (not a generic line): if what happened was genuinely bad and fact-checked, start with a "
        "short, sincere apology that names the specific thing that went wrong (not a stock 'sorry for the "
        "inconvenience' line) before reassuring them it will be looked into; if it was minor, a warm "
        "acknowledgement is enough without over-apologising. No question mark."
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
}

_CHAT_SYSTEM_PROMPT = (
    "You are Yacy's customer support assistant, chatting with a customer over WhatsApp about "
    "their grocery order. Be warm, natural, and conversational - like a helpful human agent, "
    "not a rigid script. Remember and build on what's already been said in this conversation. "
    "Hard rules that always apply: "
    "1) Only state facts given to you below or earlier in this conversation - never invent "
    "prices, dates, products, order details, or refund/delivery status. "
    "2) If something you're asked isn't covered by the facts you have, say plainly that you "
    "don't have that detail, rather than guessing or deflecting to 'check your account'. "
    "3) Never say an action (a refund, a change, a cancellation) has been completed unless the "
    "facts explicitly confirm it happened - if a customer asks for an action to be taken, "
    "explain what you can see and that this would need to go through the proper process. "
    "4) Keep replies brief (1-3 sentences) and reply with the message text only - no quotes, "
    "no preamble. "
    "5) If the customer's message is just a short acknowledgement with no actual question in it "
    "(e.g. 'ok', 'fine', 'thanks', 'great', 'sounds good'), do not reply with a generic opener "
    "like 'How can I assist you today?' or 'Let me know if you need help with anything' - that "
    "reads as ignoring what they said and restarting the conversation. Instead give a short, "
    "specific acknowledgement that responds to what they actually said, referencing the order "
    "context if it fits naturally (e.g. confirming the delivery is still on track), and only "
    "end with an offer to help further if it feels natural, not as a stock closing line. "
    "6) No emoji unless the customer used one first. "
    "7) If the customer describes something that contradicts a fact given in context (e.g. they say the order "
    "was never delivered but the facts show it was, or they blame an item that isn't actually missing), do "
    "not apologise for or confirm that specific claim as true - state the real fact plainly, while still "
    "being empathetic about whatever genuinely was an issue."
)

_CHAT_FAILURE_FALLBACK = (
    "I couldn't look that up just now - could you try asking again, or is there something "
    "else I can help with?"
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
    guidance = _KIND_GUIDANCE.get(kind)
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


def generate_chat_reply(
    session_messages: list[Dict[str, str]], grounding_facts: Dict[str, Any]
) -> Tuple[str, str]:
    """Generates a conversational reply for the open-ended assistant chat.

    Unlike `generate_message`, this sends the real back-and-forth as a proper
    `messages` array (not a single flattened blob), so the model has genuine
    multi-turn memory - it can resolve "it"/"that product"/"my previous
    order" the way a real chat does. Never cached: the whole point is that
    each reply depends on the live conversation and the specific customer's
    data, so caching here would risk leaking one customer's answer into
    another's conversation, or reusing a stale answer as the chat moves on.

    `session_messages` is the prior turns for this customer only, each
    `{"role": "user"|"assistant", "content": str}`, ending with the customer's
    latest message - the caller (the /api/messages/chat route) owns persisting
    this per customer_id.

    Returns (reply_text, source) where source is "llm" or "fallback".
    """
    client = _get_client()
    if client is None:
        return _CHAT_FAILURE_FALLBACK, "fallback"

    default_model = "gpt-4o" if os.environ.get("OPENAI_BASE_URL") else "gpt-4o-mini"
    model = os.environ.get("OPENAI_MODEL", default_model)
    system_content = f"{_CHAT_SYSTEM_PROMPT}\n\nFacts you may use (JSON, do not invent beyond this): {grounding_facts}"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_content}, *session_messages],
            max_tokens=200,
            temperature=0.6,
            timeout=8,
        )
        text = (response.choices[0].message.content or "").strip()
        if text:
            return text, "llm"
        return _CHAT_FAILURE_FALLBACK, "fallback"
    except Exception:
        logger.exception("Assistant chat reply failed")
        return _CHAT_FAILURE_FALLBACK, "fallback"


_VALID_AREAS = {"products", "delivery", "both", "none"}
_VALID_SENTIMENTS = {"negative", "positive", "neutral"}


def classify_feedback_signal(text: str, history: List[Dict[str, str]]) -> Tuple[Dict[str, Any], str]:
    """Classifies a customer's reply within the deterministic feedback-collection
    flow: which area (products/delivery/both/none) it's actually about, and
    whether it's a complaint, a retraction/satisfaction, or neutral - judged
    from real meaning, not keyword matching. This exists because a plain
    keyword scan reads "sorry i typed wrong, the delivery was great" as a
    delivery complaint (it contains the word "delivery") when it's the
    opposite. The branching logic that USES this classification stays
    deterministic code (see planFeedbackReply in the frontend) - only the
    judgement of what the customer actually meant is handed to the LLM.

    Always degrades to a safe, unopinionated classification
    (`{"area": None, "sentiment": "neutral"}`) if the LLM is unavailable or
    fails, so the caller can fall back to asking a clarifying question rather
    than guessing.
    """
    default = {"area": None, "sentiment": "neutral"}
    client = _get_client()
    if client is None:
        return default, "fallback"

    default_model = "gpt-4o" if os.environ.get("OPENAI_BASE_URL") else "gpt-4o-mini"
    model = os.environ.get("OPENAI_MODEL", default_model)
    history_text = "\n".join(f"{turn.get('speaker', '?')}: {turn.get('text', '')}" for turn in history) or "(no prior turns)"
    user_prompt = (
        f"Conversation so far:\n{history_text}\n\n"
        f'The customer just replied: "{text}"\n\n'
        "Classify this reply. Respond with ONLY a JSON object, no other text:\n"
        '{"area": "products" | "delivery" | "both" | "none", "sentiment": "negative" | "positive" | "neutral"}\n\n'
        "`area`: which topic the reply is actually about, judged from its real meaning in context - not just "
        "which words appear. If the customer is correcting an earlier statement, or clarifying that something "
        "is actually fine, reflect that in your judgement rather than pattern-matching on keywords. "
        "`sentiment`: whether THIS specific reply expresses a complaint (negative), satisfaction or a retraction "
        "of an earlier complaint (positive), or is neutral/unclear."
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You classify short customer-support replies. Reply with strict JSON only - no prose, no markdown fences."},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=50,
            temperature=0,
            timeout=6,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
        area = parsed.get("area")
        if area not in _VALID_AREAS:
            area = "none"
        sentiment = parsed.get("sentiment")
        if sentiment not in _VALID_SENTIMENTS:
            sentiment = "neutral"
        return {"area": None if area == "none" else area, "sentiment": sentiment}, "llm"
    except Exception:
        logger.exception("Feedback signal classification failed")
        return default, "fallback"


_VALID_SWAP_PREFERENCE_INTENTS = {"confirm", "decline", "unclear"}


def classify_swap_preference_intent(
    text: str, swapped_product_name: str, original_product_name: str
) -> Tuple[Dict[str, Any], str]:
    """Judges whether a free-text message on the Yacy Assistant screen means
    the customer wants `swapped_product_name` remembered as their go-to
    whenever `original_product_name` is out of stock ('confirm'), explicitly
    does NOT want that ('decline'), or the message doesn't clearly say either
    way ('unclear', e.g. small talk or an unrelated question). This decides
    whether the app actually writes the preference to the customer's profile
    - it must never default to saving on an ambiguous or declining message,
    so it always degrades to 'unclear' (no save) if the LLM is unavailable or
    fails, rather than guessing yes.
    """
    default = {"intent": "unclear"}
    client = _get_client()
    if client is None:
        return default, "fallback"

    default_model = "gpt-4o" if os.environ.get("OPENAI_BASE_URL") else "gpt-4o-mini"
    model = os.environ.get("OPENAI_MODEL", default_model)
    user_prompt = (
        f"A customer is being asked, in effect, whether they'd like '{swapped_product_name}' remembered as "
        f"their go-to replacement whenever '{original_product_name}' is out of stock.\n\n"
        f'They just typed: "{text}"\n\n'
        "Classify what they mean. Respond with ONLY a JSON object, no other text:\n"
        '{"intent": "confirm" | "decline" | "unclear"}\n\n'
        "`confirm`: they want it remembered/kept as the default (e.g. 'yes', 'ok', 'please always use that', "
        "'sounds good', 'thank you' in response to being told it's been saved). "
        "`decline`: they explicitly do NOT want it remembered as a default (e.g. 'no', 'don't swap my usual "
        "preference', 'just this once', 'leave it as it was'). "
        "`unclear`: anything else - small talk, an unrelated question, or genuinely ambiguous."
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You classify short customer-support replies. Reply with strict JSON only - no prose, no markdown fences."},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=20,
            temperature=0,
            timeout=6,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
        intent = parsed.get("intent")
        if intent not in _VALID_SWAP_PREFERENCE_INTENTS:
            intent = "unclear"
        return {"intent": intent}, "llm"
    except Exception:
        logger.exception("Swap preference intent classification failed")
        return default, "fallback"


