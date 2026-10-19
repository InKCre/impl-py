"""HTTP boundary checks for the x-monkey-compatible remote archive API."""

from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
import pytest

from app.business.peer import PeerManager
from app.database_contract.constants import JWT_ALGORITHM
from app.middleware import create_peer_jwt
from app.settings import Settings, settings
from extensions.twitter import archive_http, archive_session
from extensions.twitter.archive_schema import QueryResponse, SubmitResponse, SubmitSummary
from tests.extensions.runtime_support import publish_extension


@pytest.fixture
def archive_client(monkeypatch):
  async def current_config():
    return SimpleNamespace(http_public_base_url="https://core.example/prefix")

  monkeypatch.setattr(PeerManager, "get_current_config_async", current_config)
  app = FastAPI()
  app.include_router(archive_session.router, prefix="/twitter/x-monkey")
  app.include_router(archive_http.router, prefix="/twitter/x-monkey")
  return TestClient(app, base_url="https://core.example")


def _session_cookie() -> str:
  token, _ = archive_session._issue_session_token()
  return token


def test_session_lifetime_defaults_to_seven_days_and_supports_environment_override(
  monkeypatch,
):
  required = {
    "database_url": "postgresql+psycopg://unused:unused@127.0.0.1:1/unused",
    "jwt_secret": "test-only-jwt-secret-at-least-32-bytes",
  }
  monkeypatch.delenv("TWITTER_X_MONKEY_JWT_LIFETIME_SECONDS", raising=False)
  default = Settings(_env_file=None, **required)
  assert default.twitter_x_monkey_jwt_lifetime_seconds == 7 * 24 * 60 * 60

  monkeypatch.setenv("TWITTER_X_MONKEY_JWT_LIFETIME_SECONDS", "3600")
  overridden = Settings(_env_file=None, **required)
  assert overridden.twitter_x_monkey_jwt_lifetime_seconds == 3600


def test_connect_exchanges_peer_token_for_scoped_http_only_cookie(archive_client):
  response = archive_client.post(
    "/twitter/x-monkey/session",
    data={"token": create_peer_jwt(settings.jwt_secret)},
    headers={
      "Origin": "https://web.example",
      "Sec-Fetch-Mode": "navigate",
      "Sec-Fetch-Dest": "document",
    },
    follow_redirects=False,
  )

  assert response.status_code == 303
  assert (
    response.headers["location"] == "https://core.example/prefix/twitter/x-monkey/account"
  )
  cookie = response.headers["set-cookie"]
  assert "inkcre_twitter_archive=" in cookie
  assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=none" in cookie.lower()
  assert "Path=/prefix/twitter/x-monkey/" in cookie


def test_session_get_needs_no_json_or_client_header_and_accepts_extension_origin(
  archive_client,
):
  response = archive_client.get(
    "/twitter/x-monkey/api/v1/session",
    headers={
      "Origin": "chrome-extension://abcdefghijklmnop",
      "Accept": "application/json",
      "Cookie": f"{archive_session.SESSION_COOKIE_NAME}={_session_cookie()}",
      "Sec-Fetch-Mode": "cors",
    },
  )

  assert response.status_code == 200
  assert response.json()["authenticated"] is True
  assert (
    response.json()["account_url"] == "https://core.example/prefix/twitter/x-monkey/account"
  )
  assert "nosniff" in response.headers["x-content-type-options"]
  assert "set-cookie" not in response.headers


def test_session_renews_cookie_inside_half_life(archive_client, monkeypatch):
  now = int(time.time())
  lifetime = settings.twitter_x_monkey_jwt_lifetime_seconds
  claims = jwt.decode(
    _session_cookie(),
    options={"verify_signature": False, "verify_exp": False},
  )
  claims["iat"] = now
  claims["exp"] = now + lifetime // 2 - 1
  token = jwt.encode(claims, settings.jwt_secret, algorithm=JWT_ALGORITHM)
  monkeypatch.setattr(archive_session.time, "time", lambda: now)

  response = archive_client.get(
    "/twitter/x-monkey/api/v1/session",
    headers={
      "Cookie": f"{archive_session.SESSION_COOKIE_NAME}={token}",
      "Sec-Fetch-Mode": "cors",
    },
  )

  assert response.status_code == 200
  cookie = response.headers["set-cookie"].split(";", 1)[0]
  renewed_token = cookie.split("=", 1)[1]
  renewed_claims = archive_session._decode_peer_token(renewed_token)
  assert renewed_claims is not None
  assert renewed_claims["exp"] == now + lifetime


@pytest.mark.parametrize(
  "origin", ["null", "https://[invalid", "http://x.com", "https://unknown.example"]
)
def test_session_rejects_null_insecure_or_unknown_origin(archive_client, origin):
  response = archive_client.get(
    "/twitter/x-monkey/api/v1/session",
    headers={"Origin": origin, "Sec-Fetch-Mode": "cors"},
  )

  assert response.status_code == 403


def test_disconnect_is_limited_to_core_origin_and_clears_the_cookie(archive_client):
  headers = {
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
  }
  foreign = archive_client.post(
    "/twitter/x-monkey/disconnect",
    headers={**headers, "Origin": "https://web.example"},
  )
  assert foreign.status_code == 403

  response = archive_client.post(
    "/twitter/x-monkey/disconnect",
    headers={**headers, "Origin": "https://core.example"},
    follow_redirects=False,
  )
  assert response.status_code == 303
  assert (
    response.headers["location"] == "https://core.example/prefix/twitter/x-monkey/account"
  )
  cookie = response.headers["set-cookie"]
  assert "inkcre_twitter_archive=" in cookie
  assert "Max-Age=0" in cookie


def test_query_requires_client_headers_and_session_cookie(archive_client, monkeypatch):
  async def query(body):
    assert body.posts == ["9007199254740993"]
    return QueryResponse(users=[], posts=[], media=[])

  monkeypatch.setattr(archive_http, "query_archive", query)
  response = archive_client.post(
    "/twitter/x-monkey/api/v1/archive/query",
    json={"posts": ["9007199254740993"]},
    headers={
      "X-Tweet-DB-Client": "x-monkey",
      "Cookie": f"{archive_session.SESSION_COOKIE_NAME}={_session_cookie()}",
      "Origin": "https://x.com",
    },
  )
  assert response.status_code == 200

  no_cookie = archive_client.post(
    "/twitter/x-monkey/api/v1/archive/query",
    json={"posts": ["1"]},
    headers={"X-Tweet-DB-Client": "x-monkey", "Origin": "https://x.com"},
  )
  assert no_cookie.status_code == 401

  no_client_header = archive_client.post(
    "/twitter/x-monkey/api/v1/archive/query",
    json={"posts": ["1"]},
    headers={"Cookie": f"{archive_session.SESSION_COOKIE_NAME}={_session_cookie()}"},
  )
  assert no_client_header.status_code == 400


def test_submit_passes_validated_batch_to_native_protocol(archive_client, monkeypatch):
  async def submit(batch):
    assert batch.model_dump(exclude_unset=True) == {"posts": []}
    return SubmitResponse(
      summary=SubmitSummary(accepted=0, unchanged=0, partial=0, failed=0, total=0),
      users=[],
      posts=[],
      media=[],
    )

  monkeypatch.setattr(archive_http, "submit_archive", submit)
  response = archive_client.post(
    "/twitter/x-monkey/api/v1/archive/submit",
    json={"posts": []},
    headers={
      "X-Tweet-DB-Client": "x-monkey",
      "Cookie": f"{archive_session.SESSION_COOKIE_NAME}={_session_cookie()}",
      "Origin": "chrome-extension://abcdefghijklmnop",
    },
  )
  assert response.status_code == 200
  assert response.json()["summary"]["total"] == 0


def test_session_does_not_renew_for_no_cors_subresource(archive_client):
  response = archive_client.get(
    "/twitter/x-monkey/api/v1/session",
    headers={
      "Cookie": f"{archive_session.SESSION_COOKIE_NAME}={_session_cookie()}",
      "Sec-Fetch-Mode": "no-cors",
    },
  )
  assert response.status_code == 403
  assert "set-cookie" not in response.headers


def test_extension_publishes_claimed_archive_routes(monkeypatch):
  from extensions.twitter import Extension
  from app.business.source import SourceManager

  async def sync_source_types():
    return None

  async def current_config():
    return SimpleNamespace(http_public_base_url="https://core.example")

  monkeypatch.setattr(SourceManager, "sync_source_types_async", sync_source_types)
  monkeypatch.setattr(PeerManager, "get_current_config_async", current_config)

  published = publish_extension(Extension)
  try:
    published.client.cookies.set(archive_session.SESSION_COOKIE_NAME, _session_cookie())
    response = published.client.get("/twitter/x-monkey/api/v1/session")
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["account_url"] == "https://core.example/twitter/x-monkey/account"

    core_endpoint = published.client.get("/twitter/setup")
    assert core_endpoint.status_code == 401
  finally:
    published.unpublish()
