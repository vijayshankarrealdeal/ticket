"""HMAC visitor-token signing for the widget.

Cover happy path + tamper resistance. The shared secret is read from env
at call time, so we set a known value and rely on `compare_digest` for the
constant-time check.
"""
import os

from app.routers.widget import _sign_visitor, _verify_visitor


def setup_module(_):
    os.environ["WORKSPACEX_SECRET_KEY"] = "test-secret"


def test_sign_returns_stable_hex():
    a = _sign_visitor("acme", "v_123")
    b = _sign_visitor("acme", "v_123")
    assert a == b
    # 256-bit hex output.
    assert len(a) == 64


def test_sign_changes_with_workspace_or_visitor():
    a = _sign_visitor("acme", "v_123")
    b = _sign_visitor("globex", "v_123")
    c = _sign_visitor("acme", "v_456")
    assert a != b
    assert a != c


def test_verify_round_trip():
    token = _sign_visitor("acme", "v_123")
    assert _verify_visitor("acme", "v_123", token)


def test_verify_rejects_swap_of_workspace_or_visitor():
    token = _sign_visitor("acme", "v_123")
    assert not _verify_visitor("globex", "v_123", token)
    assert not _verify_visitor("acme", "v_456", token)


def test_verify_rejects_empty_or_missing_token():
    assert not _verify_visitor("acme", "v_123", None)
    assert not _verify_visitor("acme", "v_123", "")
    assert not _verify_visitor("", "v_123", _sign_visitor("acme", "v_123"))


def test_verify_rejects_wrong_secret():
    token = _sign_visitor("acme", "v_123")
    os.environ["WORKSPACEX_SECRET_KEY"] = "different-secret"
    try:
        assert not _verify_visitor("acme", "v_123", token)
    finally:
        os.environ["WORKSPACEX_SECRET_KEY"] = "test-secret"
