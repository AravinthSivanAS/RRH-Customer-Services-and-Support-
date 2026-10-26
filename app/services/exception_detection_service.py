from __future__ import annotations

from typing import Any, Dict, Optional

from app.models.customer import ProductPreference, SubstitutionRule


def detect_violation(
    preference: Optional[ProductPreference],
    candidate: Dict[str, Any],
    fulfilment_stock: int,
) -> Optional[str]:
    """Checks a candidate replacement product against the customer's saved
    preference for the missing item. Returns a human-readable rejection
    reason when the default swap would violate that preference, or None
    when the candidate is allowed to proceed to ranking."""

    if preference is None:
        if fulfilment_stock <= 0:
            return "Out of stock at the fulfilment store"
        return None

    if preference.substitution_rule == SubstitutionRule.SAME_BRAND_OR_ASK:
        if candidate["brand"] != preference.preferred_brand:
            return (
                f"Different brand ({candidate['brand']}) not allowed by customer "
                f"preference (SAME_BRAND_OR_ASK: {preference.preferred_brand} only)"
            )

    if fulfilment_stock <= 0:
        return "Out of stock at the fulfilment store"

    return None


def price_exceeds_limit(
    preference: Optional[ProductPreference], original_price: float, candidate_price: float
) -> bool:
    limit_pct = preference.price_increase_limit_percentage if preference else 0
    max_price = round(original_price * (1 + (limit_pct or 0) / 100), 4)
    return candidate_price > max_price + 1e-9
