from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.models.customer import ProductPreference
from app.models.event import AlternativeOption, RejectedAlternative
from app.repositories.json_repository import data_store
from app.services import exception_detection_service, inventory_service

_VOLUME_PACK_RE = re.compile(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*ml", re.IGNORECASE)
_VOLUME_ML_RE = re.compile(r"(\d+(?:\.\d+)?)\s*ml", re.IGNORECASE)
_VOLUME_L_RE = re.compile(r"(\d+(?:\.\d+)?)\s*l\b", re.IGNORECASE)


def parse_volume_liters(product_name: str) -> Optional[float]:
    match = _VOLUME_PACK_RE.search(product_name)
    if match:
        count, each_ml = match.groups()
        return (int(count) * float(each_ml)) / 1000.0
    match = _VOLUME_ML_RE.search(product_name)
    if match:
        return float(match.group(1)) / 1000.0
    match = _VOLUME_L_RE.search(product_name)
    if match:
        return float(match.group(1))
    return None


def _package_similarity_score(original_name: str, candidate_name: str) -> Tuple[int, Optional[str]]:
    original_vol = parse_volume_liters(original_name)
    candidate_vol = parse_volume_liters(candidate_name)
    if original_vol is None or candidate_vol is None or original_vol == 0:
        return 0, None
    ratio = abs(candidate_vol - original_vol) / original_vol
    score = max(0, round(20 * (1 - min(ratio, 1.0))))
    reason = f"Package size similar to original ({candidate_vol:g}L vs {original_vol:g}L)"
    return score, reason


def _quantity_score(available_quantity: int) -> int:
    return max(0, min(available_quantity, 20))


def rank_alternatives(
    original_product: Dict[str, Any],
    preference: Optional[ProductPreference],
    store_id: str,
) -> Tuple[List[AlternativeOption], List[RejectedAlternative]]:
    store_name = inventory_service.resolve_store_name(store_id)
    original_price = original_product["unit_price"]
    original_category = original_product["category"]
    original_sku = original_product["sku_id"]

    valid: List[AlternativeOption] = []
    rejected: List[RejectedAlternative] = []

    for product in data_store.inventory.data["products"]:
        if product["sku_id"] == original_sku:
            continue
        if product["category"] != original_category:
            continue

        qty_at_store = product["stock_by_store"].get(store_name, 0)

        violation_reason = exception_detection_service.detect_violation(
            preference, product, qty_at_store
        )
        if violation_reason:
            rejected.append(
                RejectedAlternative(
                    sku_id=product["sku_id"],
                    product_name=product["product_name"],
                    brand=product["brand"],
                    reason=violation_reason,
                )
            )
            continue

        if exception_detection_service.price_exceeds_limit(
            preference, original_price, product["unit_price"]
        ):
            rejected.append(
                RejectedAlternative(
                    sku_id=product["sku_id"],
                    product_name=product["product_name"],
                    brand=product["brand"],
                    reason=(
                        f"Price (£{product['unit_price']:.2f}) exceeds the customer's "
                        f"allowed price increase over the original (£{original_price:.2f})"
                    ),
                )
            )
            continue

        reasons = [
            f"Same brand: {product['brand']}",
            f"Same category: {product['category']}",
            f"In stock at {store_name} ({qty_at_store} available)",
            "Price within customer's allowed limit",
        ]
        package_score, package_reason = _package_similarity_score(
            original_product["product_name"], product["product_name"]
        )
        if package_reason:
            reasons.append(package_reason)
        qty_score = _quantity_score(qty_at_store)

        score = min(100, 60 + package_score + qty_score)

        valid.append(
            AlternativeOption(
                sku_id=product["sku_id"],
                product_name=product["product_name"],
                brand=product["brand"],
                unit_price=product["unit_price"],
                available_quantity=qty_at_store,
                score=score,
                reasons=reasons,
            )
        )

    valid.sort(key=lambda alt: alt.score, reverse=True)
    return valid, rejected
