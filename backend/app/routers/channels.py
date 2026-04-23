"""Channels subpages: E-Commerce / APIs / Integrations / Logs / Sheets / Health (spec §3.9).

Read-only stubs for Build 1 — returns empty arrays / zeros so the pages render
without errors. Wire each source when the corresponding integration ships.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from ..deps import Me

router = APIRouter(tags=["channels"])


# --- E-Commerce ---

@router.get("/ecommerce/stores")
async def ecomm_stores(user: dict = Me) -> list[dict]:
    return []


@router.get("/ecommerce/events")
async def ecomm_events(user: dict = Me) -> list[dict]:
    return []


# --- API Library ---

@router.get("/apis")
async def apis(user: dict = Me) -> list[dict]:
    return []


@router.get("/integrations")
async def integrations(user: dict = Me) -> list[dict]:
    return []


# --- API Logs ---

@router.get("/api-logs")
async def api_logs(user: dict = Me) -> list[dict]:
    return []


@router.get("/api-logs/stats")
async def api_logs_stats(user: dict = Me) -> dict:
    return {"total": 0, "success": 0, "error": 0, "successRate": 100}


# --- Sheets ---

@router.get("/sheets")
async def sheets(user: dict = Me) -> list[dict]:
    return []


# --- Health ---

@router.get("/health/services")
async def health_services(user: dict = Me) -> list[dict]:
    return []


@router.get("/incidents")
async def incidents(user: dict = Me) -> list[dict]:
    return []
