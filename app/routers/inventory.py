from __future__ import annotations

from fastapi import APIRouter

from app.models.warehouse import InventoryProduct, StoreStockResponse, WarehouseInventory
from app.services import inventory_service

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.get("", response_model=WarehouseInventory)
def get_inventory() -> WarehouseInventory:
    return inventory_service.get_all_inventory()


@router.get("/{sku_id}", response_model=InventoryProduct)
def get_product(sku_id: str) -> InventoryProduct:
    return inventory_service.get_product(sku_id)


@router.get("/{sku_id}/stores/{store_id}", response_model=StoreStockResponse)
def get_store_stock(sku_id: str, store_id: str) -> StoreStockResponse:
    return inventory_service.get_store_stock(sku_id, store_id)
