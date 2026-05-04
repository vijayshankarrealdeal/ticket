"""Presigned-URL uploads against MinIO (spec §2).

The browser POSTs `{ name, mime, size }`, gets a short-lived `uploadUrl`, PUTs
the file directly to MinIO, then attaches `key` to the message it's sending.
We never proxy bytes through FastAPI on the upload path.

Reads are different: the bucket is private, so the SPA renders attachments
via `GET /uploads/sign?key=…`, which 302-redirects to a freshly-minted
presigned GET URL. Workspace scoping is enforced by the key prefix
`{workspace_id}/…` so one tenant can't request another tenant's keys.

For prod: swap MinIO for AWS S3 / GCS and the rest stays the same — boto3 is
endpoint-driven.
"""
from __future__ import annotations

import os
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from ..deps import Me
from ..s3 import presign_get, presign_put

router = APIRouter(tags=["uploads"])


# Defaults are conservative — the operator can widen via env vars without a
# code change. Categories cover the message-attachment use case (images,
# audio/video clips, documents) and exclude executables / installers.
_DEFAULT_MIME_ALLOWLIST = (
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
    "image/heic", "image/heif",
    "audio/mpeg", "audio/wav", "audio/webm", "audio/ogg", "audio/mp4",
    "video/mp4", "video/webm", "video/quicktime",
    "application/pdf", "application/zip",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain", "text/csv", "text/markdown",
    "application/json", "application/xml",
)


def _allowed_mimes() -> set[str]:
    raw = os.environ.get("UPLOAD_MIME_ALLOWLIST")
    if not raw:
        return set(_DEFAULT_MIME_ALLOWLIST)
    return {m.strip().lower() for m in raw.split(",") if m.strip()}


def _max_upload_bytes() -> int:
    # 25 MiB by default — large enough for a phone screen recording, small
    # enough that the per-workspace MinIO quota is hard to exhaust quickly.
    raw = os.environ.get("UPLOAD_MAX_BYTES")
    try:
        return int(raw) if raw else 25 * 1024 * 1024
    except ValueError:
        return 25 * 1024 * 1024


def _sanitize(name: str) -> str:
    # keep it filesystem-safe; strip path components
    name = (name or "file").split("/")[-1].split("\\")[-1]
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:120] or "file"


@router.post("/uploads")
async def presign(body: dict, user: dict = Me) -> dict:
    """Issue a presigned PUT URL for a single file.

    Server enforces:
    - MIME must be in the allowlist (env-overridable),
    - claimed `size` must be ≤ `UPLOAD_MAX_BYTES`,
    - the presigned URL itself carries a `Content-Length-Range` so a client
      that ignores its claimed size still gets rejected at upload time,
    - keys are namespaced under `{workspace_id}/…` for read-time scoping.
    """
    name = _sanitize(body.get("name", "file"))
    mime = (body.get("mime") or "application/octet-stream").lower()
    try:
        size = int(body.get("size") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "size must be an integer")
    if size <= 0:
        raise HTTPException(400, "size required")
    max_bytes = _max_upload_bytes()
    if size > max_bytes:
        raise HTTPException(413, f"file exceeds {max_bytes} byte limit")
    allow = _allowed_mimes()
    if mime not in allow:
        raise HTTPException(415, f"mime '{mime}' not allowed")

    key = f"{user['workspace_id']}/{uuid4().hex}-{name}"
    info = presign_put(
        key,
        content_type=mime,
        content_length_range=(1, max_bytes),
    )
    return {
        **info,
        "name": name,
        "mime": mime,
        "size": size,
    }


@router.get("/uploads/sign")
async def sign_download(key: str = Query(...), user: dict = Me) -> RedirectResponse:
    """Return a 302 to a short-lived presigned GET URL for `key`. Reads are
    workspace-scoped via the key prefix — preventing cross-tenant access
    when a leaked URL turns up in logs / chat history."""
    if not key or "/" not in key:
        raise HTTPException(400, "invalid key")
    prefix, _ = key.split("/", 1)
    if prefix != user["workspace_id"]:
        # Don't leak whether the key exists; a 404 is more honest than a 403.
        raise HTTPException(404, "not found")
    url = presign_get(key)
    return RedirectResponse(url, status_code=302)
