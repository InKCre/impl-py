"""Provider-bound identities cannot be recovered from authors or current config.

These offline adapter regressions substitute provider I/O, not identity logic.
PostgreSQL collect/backfill journeys separately prove the persisted consequences.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from extensions.twitter.api import OfficialAPI, TwikitAPI


def test_official_bookmark_actor_is_the_request_account(monkeypatch):
  client = OfficialAPI(
    "app", "secret", token={}, user_id="123", user_handle="account_a", authorization_id="a"
  )

  async def request(method, endpoint, **kwargs):
    assert endpoint == "/users/{id}/bookmarks"
    assert kwargs["path_params"] == {"id": "123"}
    return {"data": [], "meta": {}}

  monkeypatch.setattr(client, "_request", request)
  result = asyncio.run(client.get_bookmarks())
  assert result.bookmark_user_id == "123"
  assert result.primary_post_ids == ()


@pytest.mark.parametrize("resolve_actor", [False, True])
def test_twikit_cookie_page_does_not_require_actor_resolution(
  monkeypatch, tmp_path, resolve_actor
):
  cookies = tmp_path / "cookies.json"
  cookies.write_text(json.dumps({"auth_token": "test-only", "ct0": "test-only"}))
  monkeypatch.setattr(TwikitAPI, "DATA_DIRECTORY", tmp_path)
  monkeypatch.setattr(TwikitAPI, "COOKIES_FILE", cookies)
  client = TwikitAPI(email="configured@example.org", username="not_the_actor", password="x")
  # Installed Twikit's real cookie login and public user_id() run here. Only
  # provider endpoints are substituted; cookie login leaves its ID cache empty.
  settings = AsyncMock(
    return_value=({"screen_name": "actual"}, None),
    side_effect=None if resolve_actor else RuntimeError("identity unavailable"),
  )
  lookup = AsyncMock(return_value=SimpleNamespace(id="123"))
  monkeypatch.setattr(client._client, "get_user_by_screen_name", lookup)
  monkeypatch.setattr(client._client.v11, "settings", settings)
  bookmarks = AsyncMock(return_value=({}, None))
  monkeypatch.setattr(client._client.gql, "bookmarks", bookmarks)

  async def fetch():
    try:
      result = await client.get_bookmarks()
      assert result.primary_post_ids == ()
      assert result.bookmark_user_id is None
      bookmarks.assert_awaited_once()
      settings.assert_not_awaited()
      lookup.assert_not_awaited()
      if resolve_actor:
        assert await client.get_authenticated_user_id() == "123"
        lookup.assert_awaited_once_with("actual")
      assert client.user_handle == "not_the_actor"
    finally:
      await client._client.http.aclose()

  asyncio.run(fetch())
