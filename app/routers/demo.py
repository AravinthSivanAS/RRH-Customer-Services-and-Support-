from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.repositories.json_repository import data_store

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/reset")
def reset_demo_data() -> Dict[str, Any]:
    data_store.reset_all()
    return {"status": "RESET_COMPLETE", "message": "All mock data restored to its original state"}
