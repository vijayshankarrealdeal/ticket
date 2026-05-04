"""Round-trip tests for the `?` → `$N` placeholder translator.

These cover the cases the regex implementation got wrong: dollar-quoted
strings, comments, E-strings, and JSONB operators that contain `?`.
"""
from app.db import _qmark_to_dollar


def test_basic_select():
    sql = "SELECT * FROM users WHERE id = ? AND email = ?"
    assert _qmark_to_dollar(sql) == "SELECT * FROM users WHERE id = $1 AND email = $2"


def test_inside_single_quoted_string_is_skipped():
    sql = "SELECT 'has ? mark' WHERE id = ?"
    assert _qmark_to_dollar(sql) == "SELECT 'has ? mark' WHERE id = $1"


def test_doubled_quote_escape_inside_string():
    sql = "SELECT 'it''s a ?' AS a, ? AS b"
    assert _qmark_to_dollar(sql) == "SELECT 'it''s a ?' AS a, $1 AS b"


def test_double_quoted_identifier():
    sql = 'SELECT "col?name" FROM t WHERE a = ?'
    assert _qmark_to_dollar(sql) == 'SELECT "col?name" FROM t WHERE a = $1'


def test_dollar_quoted_string_no_tag():
    sql = "DO $$ BEGIN RAISE NOTICE '?'; END $$ ; UPDATE t SET v = ?"
    out = _qmark_to_dollar(sql)
    # The body of the dollar-quoted block must be untouched.
    assert "$$ BEGIN RAISE NOTICE '?'; END $$" in out
    assert "v = $1" in out


def test_dollar_quoted_string_with_tag():
    sql = "SELECT $tag$ has ? inside $tag$ , ?"
    out = _qmark_to_dollar(sql)
    assert "$tag$ has ? inside $tag$" in out
    assert out.endswith(", $1")


def test_line_comment_skipped():
    sql = "SELECT 1 -- pretend ? mark\nWHERE x = ?"
    out = _qmark_to_dollar(sql)
    assert "-- pretend ? mark" in out
    assert out.endswith("WHERE x = $1")


def test_block_comment_skipped():
    sql = "SELECT /* a ? in here */ ? FROM t"
    out = _qmark_to_dollar(sql)
    assert "/* a ? in here */" in out
    assert "$1 FROM t" in out


def test_e_string_with_backslash_escape():
    sql = r"INSERT INTO t (s) VALUES (E'a\'?b'), (?)"
    out = _qmark_to_dollar(sql)
    # The E-string body should survive (including its embedded ?).
    assert r"E'a\'?b'" in out
    # The standalone ? becomes $1.
    assert out.endswith(", ($1)")


def test_jsonb_pipe_operator_preserved():
    sql = "SELECT data ?| array['a','b'] FROM t WHERE id = ?"
    out = _qmark_to_dollar(sql)
    assert "data ?|" in out
    assert "id = $1" in out


def test_jsonb_amp_operator_preserved():
    sql = "SELECT data ?& array['a','b'] FROM t WHERE id = ?"
    out = _qmark_to_dollar(sql)
    assert "data ?&" in out
    assert "id = $1" in out


def test_jsonpath_at_question_preserved():
    sql = "SELECT data @? '$.foo' FROM t WHERE id = ?"
    out = _qmark_to_dollar(sql)
    assert "data @?" in out
    assert "id = $1" in out


def test_counter_increments_correctly():
    sql = "VALUES (?, ?, ?, ?, ?, ?)"
    assert _qmark_to_dollar(sql) == "VALUES ($1, $2, $3, $4, $5, $6)"
