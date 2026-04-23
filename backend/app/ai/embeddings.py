"""Embeddings helper (spec §9, §3.11.H).

`embed(text)` returns a `bytes` blob suitable for `kb_chunks.embedding` /
`tickets.subject_embedding`. When `OPENAI_API_KEY` is set we call the real
`text-embedding-3-small` model; otherwise we fall back to a deterministic
hashed-bag-of-words sketch so the demo + tests still produce comparable
vectors. The sketch is good enough that "shipping address" and "shipping
update" land near each other under cosine — close enough to demonstrate the
semantic-search wiring without an LLM dependency.

`cosine(a, b)` returns a scalar in [-1, 1]. The agent uses it to rank
`kb_search` results when pgvector isn't available.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import struct
from typing import Iterable

DIM = 256  # local sketch dim; OpenAI returns 1536 — both modes encode to bytes
_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")


def _normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def _local_sketch(text: str) -> list[float]:
    """Hashed bag-of-words sketch — each token bumps a deterministic dim by +1.
    Gives near-zero cosine for unrelated text, ~0.6+ for shared vocabulary."""
    vec = [0.0] * DIM
    for tok in _TOKEN_RE.findall((text or "").lower()):
        h = hashlib.md5(tok.encode()).digest()
        idx = int.from_bytes(h[:4], "big") % DIM
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[idx] += sign
    return _normalize(vec)


def _from_openai(text: str) -> list[float] | None:
    """Best-effort OpenAI call. Returns None on any failure so the caller can
    fall back to the local sketch."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        import openai  # type: ignore
    except ImportError:
        return None
    try:
        client = openai.OpenAI()
        resp = client.embeddings.create(
            model="text-embedding-3-small",
            input=text[:8000] or "(empty)",
        )
        return list(resp.data[0].embedding)
    except Exception:
        return None


def embed(text: str) -> bytes:
    """Encode text into a fixed-size float32 byte string. Caller should
    `WHERE embedded_at IS NULL` to find rows that still need embedding."""
    vec = _from_openai(text) or _local_sketch(text)
    return struct.pack(f"{len(vec)}f", *vec)


def decode(blob: bytes | memoryview | None) -> list[float]:
    if not blob:
        return []
    raw = bytes(blob)
    n = len(raw) // 4
    if n == 0:
        return []
    return list(struct.unpack(f"{n}f", raw[: n * 4]))


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    a = list(a)
    b = list(b)
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n))) or 1.0
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n))) or 1.0
    return dot / (na * nb)
