"""Background worker entrypoint (spec §11).

Build 1: stub. Heartbeats, waits for signals, exits cleanly — this keeps the
docker-compose `worker` service healthy without blocking other work on Build 2.

When real workers land (AI responder, outbox dispatcher, SLA engine, health
prober, …), register them as asyncio tasks here behind an advisory-lock
acquisition so parallel worker replicas don't duplicate work.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone

from .db import get_db, close_db

log = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)


async def main() -> None:
    await get_db()
    log.info("worker booted (stub) — nothing scheduled yet, see spec §11")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        while not stop.is_set():
            # Heartbeat every 60s so you can see it's alive in `docker logs`.
            log.info("worker heartbeat at %s", datetime.now(timezone.utc).isoformat())
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
    finally:
        await close_db()
        log.info("worker exited cleanly")


if __name__ == "__main__":
    asyncio.run(main())
