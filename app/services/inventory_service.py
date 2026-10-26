from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from app.models.warehouse import InventoryProduct, StoreStockResponse, WarehouseInventory
from app.repositories.json_repository import data_store


def get_all_inventory() -> WarehouseInventory:
    return WarehouseInventory.model_validate(data_store.inventory.data)


def get_product(sku_id: str) -> InventoryProduct:
    raw = data_store.find_product(sku_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Product '{sku_id}' not found")
    return InventoryProduct.model_validate(raw)


def get_product_raw(sku_id: str) -> Dict[str, Any]:
    raw = data_store.find_product(sku_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Product '{sku_id}' not found")
    return raw


def resolve_store_name(store_id: str) -> str:
    store_name = data_store.store_name_for_id(store_id)
    if store_name is None:
        raise HTTPException(status_code=404, detail=f"Store '{store_id}' not found")
    return store_name


def get_store_stock(sku_id: str, store_id: str) -> StoreStockResponse:
    product = get_product_raw(sku_id)
    store_name = resolve_store_name(store_id)
    quantity = product["stock_by_store"].get(store_name, 0)
    return StoreStockResponse(
        sku_id=sku_id, store_id=store_id, store_name=store_name, available_quantity=quantity
    )


def get_stock_at_store(sku_id: str, store_id: str) -> int:
    product = get_product_raw(sku_id)
    store_name = resolve_store_name(store_id)
    return product["stock_by_store"].get(store_name, 0)


def decrement_stock(sku_id: str, store_id: str, quantity: int = 1) -> int:
    """Decrements stock at a store. Raises 409 if insufficient stock
    (stale-inventory protection) and never allows negative stock."""
    with data_store.lock:
        product = get_product_raw(sku_id)
        store_name = resolve_store_name(store_id)
        current = product["stock_by_store"].get(store_name, 0)
        if current < quantity:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Stale inventory: '{sku_id}' now has only {current} unit(s) "
                    f"available at '{store_name}', cannot reserve {quantity}"
                ),
            )
        product["stock_by_store"][store_name] = current - quantity
        _refresh_stock_status(product)
        return product["stock_by_store"][store_name]


def _refresh_stock_status(product: Dict[str, Any]) -> None:
    values = product["stock_by_store"].values()
    if all(v <= 0 for v in values):
        product["current_stock_status"] = "OUT_OF_STOCK"
    elif any(v <= 0 for v in values):
        product["current_stock_status"] = "PARTIALLY_AVAILABLE"
    else:
        product["current_stock_status"] = "IN_STOCK"
