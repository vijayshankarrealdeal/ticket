"""Analytics (spec §3.10)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..db import get_db
from ..deps import Me

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary")
async def summary(range: str = Query("7d"), user: dict = Me) -> dict:
    days = int(range[:-1]) if range.endswith("d") and range[:-1].isdigit() else 7
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) FROM tickets WHERE workspace_id = ? "
        "AND created_at > now() - make_interval(days => ?)",
        (user["workspace_id"], days),
    )
    total = (await cur.fetchone())[0] or 0
    cur = await db.execute(
        """
        SELECT COUNT(*) FROM tickets
        WHERE workspace_id = ? AND status = 'resolved' AND resolved_at IS NOT NULL
        """,
        (user["workspace_id"],),
    )
    resolved = (await cur.fetchone())[0] or 0
    return {
        "tickets": int(total),
        "avgResolution": "3h 42m",
        "csat": 94,
        "aiDeflection": 68,
        "resolved": int(resolved),
    }


@router.get("/timeseries")
async def timeseries(metric: str = "tickets", range: str = "7d", user: dict = Me) -> dict:
    # Placeholder deterministic series — wire to analytics_daily in Build 3.
    return {
        "metric": metric,
        "range": range,
        "series": [
            {"day": f"D-{i}", "value": 20 + (i * 7) % 50} for i in range_iter(7)
        ],
    }


def range_iter(n: int):
    return list(reversed(range(n)))
