"""Server-side enforcement on the upload endpoint.

We import the helpers directly rather than the route handler so we don't
need to spin up FastAPI for these unit tests.
"""
import os

from app.routers import uploads


def setup_function(_):
    # Keep envs clean between tests so allowlist tweaks don't leak.
    for k in ("UPLOAD_MIME_ALLOWLIST", "UPLOAD_MAX_BYTES"):
        os.environ.pop(k, None)


def test_default_mime_allowlist_includes_common_types():
    allow = uploads._allowed_mimes()
    assert "image/png" in allow
    assert "application/pdf" in allow
    # Executables / installers must NOT be in the default list — that's the
    # whole point of having an allowlist.
    assert "application/x-msdownload" not in allow
    assert "application/x-sh" not in allow


def test_env_override_replaces_allowlist():
    os.environ["UPLOAD_MIME_ALLOWLIST"] = "image/png,text/plain"
    try:
        allow = uploads._allowed_mimes()
        assert allow == {"image/png", "text/plain"}
    finally:
        del os.environ["UPLOAD_MIME_ALLOWLIST"]


def test_default_size_cap_is_25mib():
    assert uploads._max_upload_bytes() == 25 * 1024 * 1024


def test_env_overrides_size_cap():
    os.environ["UPLOAD_MAX_BYTES"] = "1048576"
    try:
        assert uploads._max_upload_bytes() == 1024 * 1024
    finally:
        del os.environ["UPLOAD_MAX_BYTES"]


def test_invalid_size_env_falls_back_to_default():
    os.environ["UPLOAD_MAX_BYTES"] = "not-a-number"
    try:
        assert uploads._max_upload_bytes() == 25 * 1024 * 1024
    finally:
        del os.environ["UPLOAD_MAX_BYTES"]


def test_sanitize_strips_path_traversal():
    assert uploads._sanitize("../../etc/passwd") == "passwd"
    assert uploads._sanitize("C:\\Windows\\System32\\evil.exe") == "evil.exe"
    assert uploads._sanitize("") == "file"
    # Allow only [A-Za-z0-9._-]; everything else collapses to underscore.
    assert uploads._sanitize("my file (1).png") == "my_file__1_.png"
