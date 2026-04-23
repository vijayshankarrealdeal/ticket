"""asyncpg-backed database layer.

Why a compat shim? Every router was written against aiosqlite's
`cursor = await db.execute(sql, params)` / `await cursor.fetchone()` /
`cursor.lastrowid` shape. Rather than rewriting a dozen routers, this module
provides a thin `DB` wrapper that:

1. Translates `?` placeholders into asyncpg's `$1, $2, …` style.
2. Converts asyncpg `Record` objects into dict-subclasses so `row["col"]`,
   `row.get("col")`, `dict(row)`, and `row[0]` all keep working.
3. Exposes a fake `Cursor` with `fetchone()` / `fetchall()` / `lastrowid` /
   `rowcount` so call sites are unchanged.
4. Makes `db.commit()` a no-op — asyncpg autocommits each `execute()` unless
   wrapped in a transaction.
5. Auto-runs [schema.sql](schema.sql) on startup so first boot bootstraps the
   DB without a separate migration step.

Postgres-specific helpers live here too (`js`, `jl`, `row_to_dict`) so routers
keep their existing import footprint.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

import asyncpg

from .config import AUTO_MIGRATE, DATABASE_URL

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


# ---------- row helpers -------------------------------------------------------


class Row(dict):
    """asyncpg Record → dict with extras so existing aiosqlite-style call sites
    keep working:
    - `row[0]` / `row["col"]` both index.
    - `(a, b) = row` unpacks the column values (matches sqlite3.Row behaviour).
    - `dict(row)` still returns a plain `{col: value}` dict.
    """

    def __init__(self, record: asyncpg.Record | dict | None):
        if record is None:
            super().__init__()
            self._values: tuple = ()
        elif isinstance(record, asyncpg.Record):
            super().__init__({k: record[k] for k in record.keys()})
            self._values = tuple(record.values())
        else:
            super().__init__(record)
            self._values = tuple(record.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


def row_to_dict(row: Row | dict | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


# ---------- placeholder rewrite ----------------------------------------------


_QUOTE_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")


def _qmark_to_dollar(sql: str) -> str:
    """Replace `?` outside quoted literals with `$1, $2, …`."""
    counter = {"n": 0}

    def _sub_safe(text: str) -> str:
        def _repl(_m: re.Match) -> str:
            counter["n"] += 1
            return f"${counter['n']}"
        return re.sub(r"\?", _repl, text)

    out: list[str] = []
    last = 0
    for m in _QUOTE_RE.finditer(sql):
        out.append(_sub_safe(sql[last:m.start()]))
        out.append(m.group(0))  # preserve quoted literals verbatim
        last = m.end()
    out.append(_sub_safe(sql[last:]))
    return "".join(out)


# ---------- cursor + DB wrappers ---------------------------------------------


class Cursor:
    """Mimics aiosqlite.Cursor for the handful of methods our routers use."""

    def __init__(self, rows: Sequence[asyncpg.Record] | list[Row] | None = None,
                 lastrowid: Any = None, rowcount: int = -1):
        self._rows: list[Row] = [Row(r) if not isinstance(r, Row) else r for r in (rows or [])]
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    async def fetchone(self) -> Row | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[Row]:
        return list(self._rows)

    async def close(self) -> None:
        self._rows = []


class DB:
    """Thin asyncpg.Pool wrapper with aiosqlite-style method surface."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        pg_sql = _qmark_to_dollar(sql)
        stripped = sql.lstrip()
        head = stripped[:6].upper()
        returns_rows = head.startswith("SELECT") or head.startswith("WITH") or " RETURNING " in sql.upper()

        async with self.pool.acquire() as conn:
            if returns_rows:
                records = await conn.fetch(pg_sql, *params)
                cur = Cursor(records)
                if records and " RETURNING " in sql.upper():
                    first = records[0]
                    if len(first) == 1:
                        cur.lastrowid = first[0]
                return cur

            status = await conn.execute(pg_sql, *params)
            # status looks like "UPDATE 3" / "DELETE 2" / "INSERT 0 4"
            parts = status.split()
            rowcount = -1
            if parts:
                try:
                    rowcount = int(parts[-1])
                except ValueError:
                    rowcount = -1
            return Cursor(rowcount=rowcount)

    async def executescript(self, sql: str) -> None:
        """Run a SQL blob with multiple statements (e.g. schema.sql)."""
        async with self.pool.acquire() as conn:
            await conn.execute(sql)

    async def commit(self) -> None:  # compatibility no-op
        return None


# ---------- lifecycle ---------------------------------------------------------


_db: DB | None = None
_lock = asyncio.Lock()


async def _wait_for_postgres(dsn: str, tries: int = 30, delay: float = 1.0) -> None:
    """Retry connecting so `docker compose up` works even if the api container
    beats Postgres to the starting line."""
    last_err: Exception | None = None
    for i in range(tries):
        try:
            conn = await asyncpg.connect(dsn, timeout=3)
            await conn.close()
            return
        except Exception as e:
            last_err = e
            if i == 0:
                log.info("waiting for postgres at %s …", _mask_dsn(dsn))
            await asyncio.sleep(delay)
    raise RuntimeError(f"postgres not reachable after {tries*delay:.0f}s: {last_err}")


def _mask_dsn(dsn: str) -> str:
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", dsn)


async def _run_migrations(db: DB) -> None:
    if not AUTO_MIGRATE:
        log.info("AUTO_MIGRATE=0 — skipping schema apply")
        return
    if not SCHEMA_FILE.exists():
        log.warning("schema.sql missing at %s — skipping migrations", SCHEMA_FILE)
        return
    log.info("applying schema.sql …")
    await db.executescript(SCHEMA_FILE.read_text())
    log.info("schema applied")


async def _maybe_seed(db: DB) -> None:
    """Run the seed exactly once: only when there are zero users. Keeps reboots
    idempotent without wiping user data."""
    from .config import AUTO_SEED
    if not AUTO_SEED:
        return
    cur = await db.execute("SELECT COUNT(*) FROM users")
    (n,) = (await cur.fetchone()) or (0,)
    if int(n or 0) > 0:
        return
    log.info("empty database → running seed (demo@workspacex.com / demo)")
    # Import lazily so seed.py can import from .db without a cycle.
    # Pass the wrapper explicitly — seed() must NOT call get_db() from here
    # because we're still inside the _lock that get_db() holds.
    from .seed import seed as _seed
    await _seed(db)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Let routers pass ISO-8601 strings for timestamp/date columns instead
    of wrapping every value in `datetime.fromisoformat(...)`. We register a
    text codec so Postgres parses the ISO string server-side on INSERT, and
    reads come back as strings that downstream helpers (`_fmt_time`, `_ago`)
    already know how to handle."""
    for typ in ("timestamptz", "timestamp", "date"):
        await conn.set_type_codec(
            typ,
            encoder=lambda v: v if isinstance(v, str) else v.isoformat(),
            decoder=str,
            format="text",
            schema="pg_catalog",
        )
    # UUIDs as strings everywhere — matches the aiosqlite-era contract and what
    # pydantic response models expect. Accepts strings on write too.
    await conn.set_type_codec(
        "uuid",
        encoder=str,
        decoder=str,
        format="text",
        schema="pg_catalog",
    )


async def get_db() -> DB:
    global _db
    if _db is not None:
        return _db
    async with _lock:
        if _db is not None:
            return _db
        await _wait_for_postgres(DATABASE_URL)
        pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=1,
            max_size=int(os.environ.get("DB_POOL_MAX", "10")),
            command_timeout=30,
            init=_init_connection,
        )
        wrapper = DB(pool)
        await _run_migrations(wrapper)
        await _maybe_seed(wrapper)
        _db = wrapper
        return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.pool.close()
        _db = None


@contextlib.asynccontextmanager
async def lifespan_db() -> AsyncIterator[None]:
    await get_db()
    try:
        yield
    finally:
        await close_db()


# ---------- JSON helpers ------------------------------------------------------


def js(value: Any) -> str:
    """Serialize a Python value for a jsonb column. asyncpg auto-decodes jsonb
    to text by default, so we pass JSON strings in and parse them on read."""
    return json.dumps(value, default=str)


def jl(value: Any, default: Any = None) -> Any:
    """Decode a jsonb column value that may already be a Python object (when
    asyncpg is configured with a JSON codec) or still a string."""
    if value is None:
        return default if default is not None else []
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    try:
        return json.loads(value)
    except Exception:
        return default if default is not None else []
