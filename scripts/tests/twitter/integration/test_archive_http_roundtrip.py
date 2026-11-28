"""The published x-monkey HTTP surface round-trips native records in PostgreSQL."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
import sqlmodel

from inkcre_core.business.peer import PeerManager
from inkcre_core.business.source import SourceManager
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from extensions.twitter.archive_session import SESSION_COOKIE_NAME, _issue_session_token
from extensions.twitter.reconcile import POST_RESOLVER, USER_RESOLVER
from tests.database import TestSession
from tests.extensions.runtime_support import publish_extension

pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated disposable PostgreSQL runtime",
)


def test_real_http_submit_query_preserves_native_wire_payload(monkeypatch, native):
  from extensions.twitter import Extension

  shared_author_id = native["users"][0]["id"]
  with TestSession() as db:
    shared_author = BlockModel(
      resolver=USER_RESOLVER,
      content=json.dumps({"id": shared_author_id}, separators=(",", ":")),
    )
    db.add(shared_author)
    db.commit()
    db.refresh(shared_author)
    shared_author_block_id = shared_author.id

  async def sync_source_types():
    return None

  async def current_config():
    return SimpleNamespace(http_public_base_url="https://core.example")

  monkeypatch.setattr(SourceManager, "sync_source_types_async", sync_source_types)
  monkeypatch.setattr(PeerManager, "get_current_config_async", current_config)
  token, _ = _issue_session_token()
  published = publish_extension(Extension)
  try:
    with published.client as client:
      client.cookies.set(SESSION_COOKIE_NAME, token)
      headers = {"Origin": "https://x.com", "X-Tweet-DB-Client": "x-monkey"}
      empty = client.post(
        "/twitter/x-monkey/api/v1/archive/submit",
        json={"posts": []},
        headers={**headers, "Origin": "chrome-extension://abcdefghijklmnop"},
      )
      assert empty.status_code == 200, empty.text
      assert empty.json() == {
        "summary": {"accepted": 0, "unchanged": 0, "partial": 0, "failed": 0, "total": 0},
        "users": [],
        "posts": [],
        "media": [],
      }
      submitted = client.post(
        "/twitter/x-monkey/api/v1/archive/submit",
        json=native,
        headers=headers,
      )
      assert submitted.status_code == 200, submitted.text
      assert submitted.json()["summary"] == {
        "total": 3,
        "accepted": 3,
        "unchanged": 0,
        "partial": 0,
        "failed": 0,
      }
      with TestSession() as db:
        citing_post = db.exec(
          sqlmodel.select(BlockModel)
          .join(RelationModel, sqlmodel.col(RelationModel.from_) == BlockModel.id)
          .where(
            RelationModel.to_ == shared_author_block_id,
            BlockModel.resolver == POST_RESOLVER,
          )
        ).one()
        assert json.loads(citing_post.content)["id"] == native["posts"][0]["id"]

      replay = client.post(
        "/twitter/x-monkey/api/v1/archive/submit", json=native, headers=headers
      )
      assert replay.status_code == 200, replay.text
      assert replay.json()["summary"] == {
        "total": 3,
        "accepted": 0,
        "unchanged": 3,
        "partial": 0,
        "failed": 0,
      }
      for kind in ("users", "posts", "media"):
        item_id = native[kind][0]["id"]
        assert submitted.json()[kind] == [{"id": item_id, "status": "accepted"}]
        assert replay.json()[kind] == [{"id": item_id, "status": "unchanged"}]

      query = {kind: [native[kind][0]["id"]] for kind in ("users", "posts", "media")}
      missing_id = str(int(native["posts"][0]["id"]) + 1000)
      query["posts"].insert(0, missing_id)
      queried = client.post(
        "/twitter/x-monkey/api/v1/archive/query",
        json=query,
        headers=headers,
      )
      assert queried.status_code == 200, queried.text
      response = queried.json()
      assert response["posts"].pop(0) == {"id": missing_id, "status": "missing"}
      for kind in ("users", "posts", "media"):
        assert response[kind] == [
          {"id": record["id"], "status": "found", "data": record} for record in native[kind]
        ]
  finally:
    published.unpublish()
