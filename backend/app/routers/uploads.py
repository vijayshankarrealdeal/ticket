"""Presigned-URL uploads against MinIO (spec §2).

The browser POSTs `{ name, mime, size }`, gets a short-lived `uploadUrl`, PUTs
the file directly to MinIO, then attaches `publicUrl` to the message it's
sending. We never proxy the bytes through FastAPI.

For prod: swap MinIO for AWS S3 / GCS and the rest stays the same — boto3 is
endpoint-driven.
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter

from ..deps import Me
from ..s3 import presign_put

router = APIRouter(tags=["uploads"])


def _sanitize(name: str) -> str:
    # keep it filesystem-safe; strip path components
    name = (name or "file").split("/")[-1].split("\\")[-1]
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:120] or "file"


@router.post("/uploads")
async def presign(body: dict, user: dict = Me) -> dict:
    name = _sanitize(body.get("name", "file"))
    key = f"{user['workspace_id']}/{uuid4().hex}-{name}"
    info = presign_put(key, content_type=body.get("mime"))
    return {
        **info,
        "name": name,
        "mime": body.get("mime"),
        "size": body.get("size"),
    }
