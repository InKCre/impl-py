"""A connected OAuth account feeds the official Bookmark source through X's wire format."""

import asyncio
import base64
import hashlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx

from app.middleware import create_peer_jwt
from app.settings import settings
from extensions.twitter import Extension
from extensions.twitter.api import TwitterAPI
from inkcre_core.business.peer import PeerManager
from inkcre_core.business.source import SourceManager
from tests.extensions.runtime_support import publish_extension


def test_connect_account_and_read_official_bookmark_page(monkeypatch):
  async def config():
    return SimpleNamespace(http_public_base_url="https://core.example/prefix")

  async def sync_types():
    pass

  callback_url = "https://core.example/prefix/twitter/auth/callback"
  requests = []
  challenge = None
  cursor = "next-page"

  async def provider(_transport, request):
    requests.append((request.method, request.url.path))
    assert request.url.host == "api.x.com"
    if request.url.path == "/2/oauth2/token":
      fields = parse_qs(request.content.decode())
      assert fields["code"] == ["test-code"]
      assert fields["redirect_uri"] == [callback_url]
      digest = hashlib.sha256(fields["code_verifier"][0].encode()).digest()
      assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
      return httpx.Response(
        200, json={"access_token": "fixture-token", "token_type": "Bearer"}
      )
    assert request.headers["Authorization"] == "Bearer fixture-token"
    if request.url.path == "/2/users/me":
      return httpx.Response(200, json={"data": {"id": "17", "username": "collector"}})
    assert request.url.path == "/2/users/17/bookmarks"
    assert request.url.params["pagination_token"] == cursor
    assert request.url.params["max_results"] == "5"
    assert "referenced_tweets.id" in request.url.params["expansions"].split(",")
    return httpx.Response(
      200,
      json={
        "data": [
          {
            "id": "31",
            "author_id": "17",
            "created_at": "2026-10-19T01:00:00Z",
            "text": "short…",
            "note_tweet": {"text": "@reply 原始🌏 &amp; https://t.co/media"},
            "conversation_id": "1",
            "referenced_tweets": [{"type": "replied_to", "id": "2"}],
          }
        ],
        "includes": {
          "tweets": [
            {
              "id": "2",
              "author_id": "17",
              "created_at": "2026-10-18T01:00:00Z",
              "text": "parent &amp; reply",
            }
          ]
        },
        "meta": {"next_token": "next-again"},
      },
    )

  monkeypatch.setattr(PeerManager, "get_current_config_async", config)
  monkeypatch.setattr(SourceManager, "sync_source_types_async", sync_types)
  monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", provider)
  published = publish_extension(Extension)
  try:
    client = published.client
    headers = {"Authorization": f"Bearer {create_peer_jwt(settings.jwt_secret)}"}
    configured = client.put(
      "/twitter/setup/oauth-app",
      json={"client_id": "fixture-client", "client_secret": "fixture-secret"},
      headers=headers,
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["callback_url"] == callback_url
    started = client.post("/twitter/setup/oauth-transactions", headers=headers)
    assert started.status_code == 200, started.text
    transaction = started.json()
    authorize = parse_qs(urlsplit(transaction["authorize_url"]).query)
    challenge = authorize["code_challenge"][0]
    assert authorize["code_challenge_method"] == ["S256"]
    outcome = client.get(
      "/twitter/auth/callback", params={"code": "test-code", "state": authorize["state"][0]}
    )
    assert outcome.status_code == 200, outcome.text
    polled = client.post(
      "/twitter/setup/oauth-transaction",
      json={"transaction_id": transaction["id"]},
      headers=headers,
    )
    assert polled.json()["status"] == "succeeded"
    status = client.get("/twitter/setup", headers=headers).json()
    assert status["connected"] is True and status["handle"] == "collector"

    async def bookmarks():
      api = await TwitterAPI.new()
      return await api.get_bookmarks(max_results=5, page=cursor)

    page = asyncio.run(bookmarks())
    assert page.next_page == "next-again"
    assert page.primary_post_ids == ("31",)
    post, parent = page.batch.posts
    assert parent.id == "2" and parent.text is not None
    assert parent.text.text == "parent & reply"
    assert post.replyTo is not None
    assert post.conversationId == "1" and post.replyTo.postId == "2"
    assert post.text is not None and post.text.text == "@reply 原始🌏 & https://t.co/media"
    assert requests == [
      ("POST", "/2/oauth2/token"),
      ("GET", "/2/users/me"),
      ("GET", "/2/users/17/bookmarks"),
    ]
    assert (
      client.delete("/twitter/setup/account", headers=headers).json()["connected"] is False
    )
  finally:
    published.unpublish()
