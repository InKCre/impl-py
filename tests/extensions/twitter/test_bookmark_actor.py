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
    if endpoint == "/users/me":
      return {"data": {"id": "456", "username": "account_b"}}
    assert endpoint == "/users/{id}/bookmarks"
    assert kwargs["path_params"] == {"id": "123"}
    # Change the client's current identity before the awaited page returns.
    # The page must retain the identity actually used by this request.
    await client.get_user()
    return {"data": [], "meta": {}}

  monkeypatch.setattr(client, "_request", request)
  result = asyncio.run(client.get_bookmarks())
  assert client.user_id == "456"
  assert result.bookmark_user_id == "123"
  assert result.primary_post_ids == ()


@pytest.mark.parametrize("identity_available", [True, False])
def test_twikit_cookie_login_resolves_authenticated_actor(
  monkeypatch, tmp_path, identity_available
):
  cookies = tmp_path / "cookies.json"
  cookies.write_text(json.dumps({"auth_token": "test-only", "ct0": "test-only"}))
  monkeypatch.setattr(TwikitAPI, "DATA_DIRECTORY", tmp_path)
  monkeypatch.setattr(TwikitAPI, "COOKIES_FILE", cookies)
  client = TwikitAPI(email="configured@example.org", username="not_the_actor", password="x")
  # Installed Twikit's real cookie login and public user_id() run here. Only
  # provider endpoints are substituted; cookie login leaves its ID cache empty.
  client._client.v11.settings = AsyncMock(return_value=({"screen_name": "actual"}, None))
  lookup = AsyncMock(
    return_value=SimpleNamespace(id="123"),
    side_effect=None if identity_available else RuntimeError("identity unavailable"),
  )
  monkeypatch.setattr(client._client, "get_user_by_screen_name", lookup)
  client._client.gql.bookmarks = AsyncMock(return_value=({}, None))

  async def fetch():
    try:
      return await client.get_bookmarks()
    finally:
      await client._client.http.aclose()

  if not identity_available:
    with pytest.raises(RuntimeError, match="identity unavailable"):
      asyncio.run(fetch())
  else:
    result = asyncio.run(fetch())
    assert result.bookmark_user_id == "123"
    assert result.primary_post_ids == ()
    assert client.user_handle == "not_the_actor"
