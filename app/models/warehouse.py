from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel


class StoreInfo(BaseModel):
    store_id: str
    store_name: str


class ProductLocation(BaseModel):
    zone: str
    aisle: str
    bay: str
    shelf: str


class InventoryProduct(BaseModel):
    sku_id: str
    product_name: str
    brand: str
    category: str
    unit_price: float
    product_location: ProductLocation
    current_stock_status: str
    stock_by_store: Dict[str, int]


class WarehouseInventory(BaseModel):
    inventory_last_updated: str
    stores: List[StoreInfo]
    products: List[InventoryProduct]


class StoreStockResponse(BaseModel):
    sku_id: str
    store_id: str
    store_name: str
    available_quantity: int
