"""Shared pytest fixtures.

The bulk of the suite is unit tests against pure functions (placeholder
translator, HMAC signer, MIME allowlist). They run without Postgres / MinIO.
Integration tests that need the database use the `db` fixture which spins up
a temporary asyncpg pool against `TEST_DATABASE_URL` and skip when unset —
so a developer with Postgres running can `pytest -m integration` while CI
runs the unit suite by default.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Tests live under backend/tests; the package is rooted at backend/.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless TEST_DATABASE_URL is set."""
    if os.environ.get("TEST_DATABASE_URL"):
        return
    import pytest
    skip_integration = pytest.mark.skip(
        reason="set TEST_DATABASE_URL to run integration tests"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
