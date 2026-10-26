from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app.routers import customers, demo, events, inventory, messages, notifications, orders

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("yacy.near")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Yacy NEAR - Anticipate backend started")
    yield


app = FastAPI(
    title="Yacy NEAR - Anticipate",
    description=(
        "Backend proof-of-concept demonstrating how Yacy proactively manages an "
        "out-of-stock product before an online grocery order leaves the store."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(customers.router)
app.include_router(inventory.router)
app.include_router(orders.router)
app.include_router(events.router)
app.include_router(notifications.router)
app.include_router(demo.router)
app.include_router(messages.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "service": "yacy-near-anticipate"}
