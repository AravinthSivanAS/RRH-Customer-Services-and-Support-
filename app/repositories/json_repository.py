from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any, Dict, List

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class JsonRepository:
    """Loads a JSON mock-database file into memory and keeps a pristine
    seed copy so the dataset can be restored on demand (demo reset)."""

    def __init__(self, filename: str):
        self._path = DATA_DIR / filename
        self._seed: Any = json.loads(self._path.read_text(encoding="utf-8"))
        self.data: Any = copy.deepcopy(self._seed)

    def save(self) -> None:
        """Writes the current in-memory state back to the JSON file on disk -
        only called for repositories where the demo intentionally persists
        changes (currently just orders), so a real substitution/selection is
        visible if you open the file while the demo is running."""
        self._path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def reset(self, persist: bool = False) -> None:
        self.data = copy.deepcopy(self._seed)
        if persist:
            self.save()


class DataStore:
    """Aggregates all in-memory JSON repositories plus the transient,
    non-persisted runtime state needed to run the short-pick-to-substitution
    workflow (substitution cases, idempotency guards)."""

    def __init__(self):
        self.lock = threading.RLock()
        self._load_all()

    def _load_all(self) -> None:
        self.customers = JsonRepository("customer_360.json")
        self.inventory = JsonRepository("warehouse_inventory.json")
        self.orders = JsonRepository("orders.json")
        self.events = JsonRepository("events.json")
        self.notifications = JsonRepository("notifications.json")

        # Runtime-only state, rebuilt on every reset.
        self.substitution_cases: Dict[str, Any] = {}
        self.processed_short_picks: set[str] = set()

    def reset_all(self) -> None:
        with self.lock:
            self.customers.reset(persist=True)
            self.inventory.reset()
            self.orders.reset(persist=True)
            self.events.reset()
            self.notifications.reset()
            self.substitution_cases = {}
            self.processed_short_picks = set()

    # --- convenience accessors -------------------------------------------------

    def find_customer(self, customer_id: str) -> Dict[str, Any] | None:
        for customer in self.customers.data:
            if customer["customer_id"] == customer_id:
                return customer
        return None

    def find_order(self, order_id: str) -> Dict[str, Any] | None:
        for order in self.orders.data:
            if order["order_id"] == order_id:
                return order
        return None

    def find_product(self, sku_id: str) -> Dict[str, Any] | None:
        for product in self.inventory.data["products"]:
            if product["sku_id"] == sku_id:
                return product
        return None

    def store_name_for_id(self, store_id: str) -> str | None:
        for store in self.inventory.data["stores"]:
            if store["store_id"] == store_id:
                return store["store_name"]
        return None

    def find_notification(self, notification_id: str) -> Dict[str, Any] | None:
        for notification in self.notifications.data:
            if notification["notification_id"] == notification_id:
                return notification
        return None

    def events_for_order(self, order_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.events.data if e["order_id"] == order_id]


data_store = DataStore()
