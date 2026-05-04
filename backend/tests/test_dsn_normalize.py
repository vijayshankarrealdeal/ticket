"""DSN normalisation: only the `ssl` query key gets rewritten to `sslmode`."""
from app.config import _normalize_pg_dsn


def test_no_query_left_alone():
    dsn = "postgresql://u:p@h:5432/db"
    assert _normalize_pg_dsn(dsn) == dsn


def test_ssl_key_rewritten_to_sslmode():
    dsn = "postgresql://u:p@h:5432/db?ssl=require"
    assert _normalize_pg_dsn(dsn) == "postgresql://u:p@h:5432/db?sslmode=require"


def test_unrelated_param_with_ssl_value_untouched():
    """The old `.replace('?ssl=', '?sslmode=')` would corrupt this case."""
    dsn = "postgresql://u:p@h:5432/db?application_name=ssl-app"
    out = _normalize_pg_dsn(dsn)
    # No rewrite — the param key isn't `ssl`.
    assert "application_name=ssl-app" in out
    assert "sslmode" not in out


def test_ssl_key_among_other_params():
    dsn = "postgresql://u:p@h:5432/db?ssl=require&application_name=svc"
    out = _normalize_pg_dsn(dsn)
    assert "sslmode=require" in out
    assert "application_name=svc" in out
    assert "ssl=require" not in out
