"""Topic-subscribe authorization rules.

These exercise the kinds-of-topic that can be checked without hitting the
DB (`inbox`, `notif`, etc). The conv/ticket/teams branches require a live
database and live in the integration suite.
"""
import pytest

from app.ws import _authorize_topic


@pytest.mark.asyncio
async def test_inbox_matches_user_workspace():
    user = {"workspace_id": "ws-1", "id": "u-1"}
    assert await _authorize_topic(user, "inbox:ws-1") is True


@pytest.mark.asyncio
async def test_inbox_rejects_other_workspace():
    user = {"workspace_id": "ws-1", "id": "u-1"}
    assert await _authorize_topic(user, "inbox:ws-2") is False


@pytest.mark.asyncio
async def test_notif_matches_user_id_only():
    user = {"workspace_id": "ws-1", "id": "u-1"}
    assert await _authorize_topic(user, "notif:u-1") is True
    assert await _authorize_topic(user, "notif:u-2") is False


@pytest.mark.asyncio
async def test_unknown_topic_kind_denied():
    user = {"workspace_id": "ws-1", "id": "u-1"}
    assert await _authorize_topic(user, "secret:everything") is False


@pytest.mark.asyncio
async def test_malformed_topic_denied():
    user = {"workspace_id": "ws-1", "id": "u-1"}
    assert await _authorize_topic(user, "") is False
    assert await _authorize_topic(user, "no_colon") is False
    assert await _authorize_topic(user, "inbox:") is False


@pytest.mark.asyncio
async def test_presence_and_agents_match_workspace():
    user = {"workspace_id": "ws-1", "id": "u-1"}
    assert await _authorize_topic(user, "presence:ws-1") is True
    assert await _authorize_topic(user, "agents:ws-1") is True
    assert await _authorize_topic(user, "presence:ws-2") is False
