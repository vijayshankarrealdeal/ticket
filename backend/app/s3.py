"""MinIO / S3 integration for presigned uploads.

MinIO is S3-compatible, so we use boto3 with a custom endpoint. The flow is
the standard S3 browser-upload pattern:

1. Frontend asks for a presigned PUT URL (`POST /uploads`).
2. Frontend PUTs the file directly to `upload_url` with the right Content-Type.
3. Frontend stores the object key (or a backend-proxied download URL) in
   `messages.attachments`. To render it later it asks the backend to mint
   a short-lived presigned GET URL via `GET /uploads/sign?key=<key>`.

The bucket is **private**: the previous implementation set a wildcard
public-read policy, which made every uploaded object world-readable on
every URL leak. Reads now go through presigned GETs, scoped to the caller's
workspace by the key prefix `{workspace_id}/…`.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import (
    S3_ACCESS_KEY,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_PRESIGN_EXPIRES,
    S3_PUBLIC_URL,
    S3_REGION,
    S3_SECRET_KEY,
)

log = logging.getLogger(__name__)


def _client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket() -> None:
    """Create the workspace bucket if it doesn't exist. Called once on startup.

    The bucket stays **private** — every read goes through a presigned GET.
    A previous implementation set a wildcard public-read policy on every
    boot; we deliberately do not reset that here so existing buckets can
    be hardened with a manual `aws s3api delete-bucket-policy` (or its
    MinIO equivalent) without this code re-applying the public policy.
    """
    try:
        c = _client()
        try:
            c.head_bucket(Bucket=S3_BUCKET)
            return
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchBucket", "NotFound"):
                log.warning("head_bucket(%s) returned %s — attempting create", S3_BUCKET, code)
        c.create_bucket(Bucket=S3_BUCKET)
        log.info("minio bucket '%s' ready at %s (private)", S3_BUCKET, S3_ENDPOINT_URL)
    except (BotoCoreError, ClientError, Exception) as e:
        # Don't fail boot if minio isn't reachable — uploads just won't work.
        # The operator can recheck via /healthz and fix the container.
        log.warning("could not ensure minio bucket: %s", e)


def put_json(key: str, payload: Any) -> str | None:
    """PUT a JSON payload directly via the server-side S3 client. Returns the
    bucket key on success, None on any failure (MinIO unreachable, auth, etc).
    Used for durable flow-config storage — the Agent Builder chat playground
    re-hydrates these back into Langflow when a restart wipes its DB.
    """
    try:
        c = _client()
        body = json.dumps(payload or {}).encode("utf-8")
        c.put_object(
            Bucket=S3_BUCKET, Key=key, Body=body,
            ContentType="application/json",
        )
        return key
    except (BotoCoreError, ClientError, Exception) as e:
        log.warning("s3 put_json(%s) failed: %s", key, e)
        return None


def get_json(key: str) -> Any | None:
    """GET a JSON payload previously written with `put_json`. Returns the
    parsed object or None if missing / unreadable."""
    try:
        c = _client()
        obj = c.get_object(Bucket=S3_BUCKET, Key=key)
        body = obj["Body"].read()
        return json.loads(body.decode("utf-8"))
    except (BotoCoreError, ClientError, Exception) as e:
        log.info("s3 get_json(%s) miss: %s", key, e)
        return None


def presign_put(key: str,
                content_type: str | None = None,
                content_length_range: tuple[int, int] | None = None) -> dict:
    """Return {upload_url, key, expires_in} for a direct browser PUT.

    `upload_url` goes to MinIO's internal address (inside docker that's
    `http://minio:9000`). The browser-visible host is `S3_PUBLIC_URL`.

    `content_length_range` clamps the body size at the SignedURL layer:
    MinIO/S3 reject the upload if `Content-Length` falls outside the range.
    Pair with a server-side allowlist + size cap in the route handler so
    a malicious client can't claim a small `size` and PUT a large file.
    """
    c = _client()
    params: dict[str, Any] = {"Bucket": S3_BUCKET, "Key": key}
    if content_type:
        params["ContentType"] = content_type
    if content_length_range:
        params["ContentLengthRange"] = list(content_length_range)
    upload_url = c.generate_presigned_url(
        "put_object", Params=params, ExpiresIn=S3_PRESIGN_EXPIRES, HttpMethod="PUT"
    )
    # Rewrite internal endpoint → browser-visible endpoint.
    if S3_PUBLIC_URL and S3_PUBLIC_URL != S3_ENDPOINT_URL:
        upload_url = upload_url.replace(S3_ENDPOINT_URL, S3_PUBLIC_URL, 1)
    return {
        "upload_url": upload_url,
        "uploadUrl": upload_url,       # frontend naming
        "key": key,
        "expires_in": S3_PRESIGN_EXPIRES,
    }


def presign_get(key: str, expires_in: int | None = None) -> str:
    """Return a short-lived presigned GET URL for `key`. The bucket is private,
    so reads must always go through this — including image previews rendered
    in the SPA. The /uploads/sign route wraps this with a workspace check."""
    c = _client()
    url = c.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=int(expires_in or S3_PRESIGN_EXPIRES),
        HttpMethod="GET",
    )
    if S3_PUBLIC_URL and S3_PUBLIC_URL != S3_ENDPOINT_URL:
        url = url.replace(S3_ENDPOINT_URL, S3_PUBLIC_URL, 1)
    return url
