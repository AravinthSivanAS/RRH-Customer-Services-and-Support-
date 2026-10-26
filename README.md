# Yacy NEAR – Anticipate

A backend-only proof-of-concept showing how Yacy proactively manages an
out-of-stock product **before** an online grocery order leaves the store —
instead of Maya finding out at the kitchen counter twelve hours later.

## Business scenario

Maya is a Gold-tier Yacy customer whose son only drinks Meadow Lactose-Free
Milk. She has saved a `SAME_BRAND_OR_ASK` substitution rule for that product.
On the morning of delivery, a picker at Leeds Central finds the shelf empty.
The backend:

1. Receives the short-pick event the instant it happens (not in a nightly batch).
2. Looks up Maya's Customer 360 profile and her saved substitution preference.
3. Checks live stock for the SKU across three stores.
4. Finds same-brand, same-category, in-stock, same-price alternatives.
5. Blocks any alternative that violates her preference (wrong brand, out of
   stock at the fulfilment store, or too expensive) — with a reason.
6. Ranks the valid alternatives and sends a single proactive WhatsApp-style
   notification with the options, before the van has left.
7. Accepts Maya's selected replacement, re-validates stock (guarding against
   a stale offer), updates the order and decrements inventory.
8. If nothing eligible exists, routes the case to a human advisor as a
   `HUMAN_ASSISTANCE_REQUIRED` case (e.g. `GOLD_CUSTOMER_CRITICAL_PREFERENCE`)
   with mock wallet-credit / refund options.
9. Records every decision as an auditable event, so the full story — what
   happened, why, and who decided it — can be replayed on the order timeline.

## Architecture

- **FastAPI** app exposing REST endpoints only (no UI).
- **In-memory JSON repositories**: each `app/data/*.json` file is loaded once
  at startup into memory. All reads/writes happen against that in-memory
  copy; a pristine deep copy is kept so `POST /api/demo/reset` can restore
  the original dataset for a repeatable demo, without touching disk.
- **Layering**: `routers/` (HTTP I/O, request/response shaping) →
  `services/` (business rules: preference checks, exception detection,
  next-best-action ranking, notification lifecycle, order mutation,
  auditing) → `repositories/` (the in-memory data store).
- A `threading.RLock` on the shared data store keeps concurrent mutations
  (stock decrements, order updates) consistent within a single process.

## Folder structure

```
app/
  main.py                     FastAPI app, router wiring, /health
  models/                     Pydantic schemas (customer, warehouse, order, event, notification)
  routers/                    HTTP endpoints (customers, inventory, orders, events, notifications, demo)
  services/                   Business logic
    customer_service.py         Customer 360 lookups, preference upsert
    inventory_service.py        Stock lookups & decrements, stale-inventory guard
    preference_service.py       Finds a customer's saved preference for a SKU/category
    exception_detection_service.py  Flags a default swap that violates the saved rule
    next_best_action_service.py Ranks eligible alternatives, rejects the rest with reasons
    order_service.py            Order mutation, substitution application, dispatch lock
    notification_service.py     Mock WhatsApp notification lifecycle
    audit_service.py            Event + audit trail, order timeline
  repositories/
    json_repository.py        Generic JSON-backed in-memory repo + the shared DataStore
  data/                      Seed JSON "databases"
  tests/                     Pytest suite
requirements.txt
README.md
```

## Installation

```bash
pip install -r requirements.txt
```

## Running the server

```bash
uvicorn app.main:app --reload
```

Swagger UI: http://localhost:8000/docs

Health check: http://localhost:8000/health

## Running the tests

```bash
pytest
```

## Example curl commands

Report a short pick (triggers the full anticipation workflow):

```bash
curl -X POST http://localhost:8000/api/events/short-pick \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "SHORT_PICK_RECORDED",
    "event_time": "2026-10-27T06:30:00Z",
    "order_id": "ORD-2026-10001",
    "customer_id": "CUST-1001",
    "store_id": "STORE-LEEDS-CENTRAL",
    "sku_id": "SKU-MEADOW-LF-1L",
    "requested_quantity": 1,
    "available_quantity": 0,
    "picker_id": "PICKER-204"
  }'
```

Check the offered/rejected alternatives for the order:

```bash
curl http://localhost:8000/api/orders/ORD-2026-10001/substitution-options
```

Accept a replacement (use the `notification_id` returned above):

```bash
curl -X POST http://localhost:8000/api/orders/ORD-2026-10001/substitution-selection \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST-1001",
    "original_sku_id": "SKU-MEADOW-LF-1L",
    "selected_sku_id": "SKU-MEADOW-LF-2L",
    "notification_id": "<notification-id-from-the-short-pick-response>"
  }'
```

Or respond directly from the notification thread:

```bash
curl -X POST http://localhost:8000/api/notifications/<notification_id>/simulate-response \
  -H "Content-Type: application/json" \
  -d '{"selected_sku_id": "SKU-MEADOW-LF-2L"}'
```

Replay the full audit trail for the order:

```bash
curl http://localhost:8000/api/orders/ORD-2026-10001/timeline
```

Reset the demo data back to its seeded state:

```bash
curl -X POST http://localhost:8000/api/demo/reset
```

## Complete demo flow (via Swagger)

1. `GET /api/inventory/SKU-MEADOW-LF-1L/stores/STORE-LEEDS-CENTRAL` — confirm
   the Meadow 1L carton is at 0 units in Leeds Central.
2. `POST /api/events/short-pick` with the payload above — see the system
   find two same-brand alternatives, reject the Yacy brand swap, and send a
   `SENT` WhatsApp notification, all synchronously in one response.
3. `GET /api/orders/ORD-2026-10001/substitution-options` — inspect the full
   decision: valid vs. rejected alternatives and why.
4. `POST /api/orders/ORD-2026-10001/substitution-selection` (or the
   notification's `simulate-response`) — approve `SKU-MEADOW-LF-2L`.
5. `GET /api/orders/ORD-2026-10001` — see the original line marked
   `SUBSTITUTED`, the new line `APPROVED_SUBSTITUTION`, and the order total
   unchanged (same price).
6. `GET /api/inventory/SKU-MEADOW-LF-2L/stores/STORE-LEEDS-CENTRAL` — stock
   decremented by one.
7. `GET /api/orders/ORD-2026-10001/timeline` — the complete, chronological
   event + audit trail for the case.
8. To see the "no eligible alternative" path, zero out both
   `SKU-MEADOW-LF-2L` and `SKU-MEADOW-LF-6X200ML` stock at Leeds Central
   first (or run a fresh short-pick for a SKU with no alternatives), then
   repeat step 2 — the response outcome becomes `HUMAN_ASSISTANCE_REQUIRED`
   with mock wallet-credit / refund options.
9. `POST /api/demo/reset` — restore the original dataset for the next run.

## Assumptions and limitations

- This is a single-process, in-memory demo: there is no real database, no
  persistence across restarts, and no distributed locking. A `threading.RLock`
  protects the shared in-memory state within one process.
- `customer_360.json`'s embedded `active_order` is seed/display data; the
  live, mutable system of record for order state is `orders.json` via
  `order_service`, seeded from the same values at startup.
- WhatsApp delivery is fully simulated: a notification is created and its
  status immediately recorded as `READY_TO_SEND` → `SENT` in its
  `status_history`; it moves to `RESPONDED` only when the customer (or an
  advisor on her behalf) acts on it. No real WhatsApp/Twilio integration.
- "Stale inventory" protection re-checks and decrements stock at the moment
  of substitution selection; if another request (or a test) has reduced
  stock below the requested quantity since the offer was made, the selection
  endpoint returns `409 Conflict`.
- Short-pick idempotency is tracked per `(order_id, sku_id)` pair in memory;
  replaying the exact same short-pick for that pair returns `409 Conflict`.
- Package-size similarity scoring (for next-best-action ranking) is a simple
  heuristic that parses litres/millilitres out of the product name (e.g.
  `1L`, `2L`, `6 x 200ml`); it is a reasonable demo approximation, not a
  general-purpose unit parser.
- As the "Near" narrative states: this backend reacts at the moment of
  picking. A supplier's short delivery that was visible days earlier is out
  of scope — that is the "Next" stage, not modelled here.
