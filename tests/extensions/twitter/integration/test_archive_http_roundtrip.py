"""The published x-monkey HTTP surface round-trips native records in PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
import sqlalchemy
import sqlmodel

from app.persistence.info_base.uow import graph_uow
from app.business.peer import PeerManager
from app.business.source import SourceManager
from app.schemas.info_base.block import BlockModel
from app.schemas.info_base.relation import RelationModel
from extensions.twitter.archive_session import SESSION_COOKIE_NAME, _issue_session_token
from extensions.twitter.reconcile import MEDIA_RESOLVER, POST_RESOLVER, USER_RESOLVER
from tests.database import TestSession
from tests.extensions.runtime_support import publish_extension

pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated disposable PostgreSQL runtime",
)


def _distinct_fixture() -> dict:
  path = Path(__file__).parents[1] / "fixtures" / "native_archive.json"
  value = json.loads(path.read_text())
  offset = int(uuid.uuid4().hex[:15], 16) + 10**20

  def distinct(item):
    if isinstance(item, str):
      if item.isascii() and item.isdecimal():
        return str(int(item) + offset)
      if item.startswith(("https://pbs.twimg.com/", "https://video.twimg.com/")):
        return f"{item}{'&' if '?' in item else '?'}twitter_http_test={offset}"
    if isinstance(item, list):
      return [distinct(child) for child in item]
    if isinstance(item, dict):
      return {key: distinct(child) for key, child in item.items()}
    return item

  return distinct(value)


def _all_archive_ids(native: dict) -> tuple[str, ...]:
  return tuple(
    record["id"] for kind in ("users", "posts", "media") for record in native[kind]
  )


async def _cleanup_archive(native_ids: tuple[str, ...]) -> None:
  async with graph_uow() as uow:
    roots = await uow.blocks.find_json_field_many(
      (POST_RESOLVER, USER_RESOLVER, MEDIA_RESOLVER), "id", native_ids
    )
    tracked_ids = {block.id for block in roots if block.id is not None}
    relations = await uow.relations.get_outgoing_many(tracked_ids)
    tracked_ids.update(relation.to_ for relation in relations)

  if not tracked_ids:
    return
  with TestSession() as db:
    for relation in db.exec(
      sqlmodel.select(RelationModel).where(
        sqlalchemy.or_(
          sqlmodel.col(RelationModel.from_).in_(tracked_ids),
          sqlmodel.col(RelationModel.to_).in_(tracked_ids),
        )
      )
    ).all():
      db.delete(relation)
    for block in db.exec(
      sqlmodel.select(BlockModel).where(sqlmodel.col(BlockModel.id).in_(tracked_ids))
    ).all():
      db.delete(block)
    db.commit()


def test_real_http_submit_query_preserves_native_wire_payload(monkeypatch):
  from extensions.twitter import Extension

  native = _distinct_fixture()
  simple_post_id = str(int(uuid.uuid4().hex[:15], 16) + 10**30)
  simple_author_id = str(int(simple_post_id) + 1)
  native_ids = (*_all_archive_ids(native), simple_post_id)

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
      simple_post = {
        "id": simple_post_id,
        "authorId": simple_author_id,
        "createdAt": "2026-10-19T12:00:00Z",
        "text": {"text": "HTTP to PostgreSQL round trip"},
      }
      simple_submit = client.post(
        "/twitter/x-monkey/api/v1/archive/submit",
        json={"posts": [simple_post]},
        headers=headers,
      )
      assert simple_submit.status_code == 200, simple_submit.text
      simple_query = client.post(
        "/twitter/x-monkey/api/v1/archive/query",
        json={"posts": [simple_post_id]},
        headers=headers,
      )
      assert simple_query.status_code == 200, simple_query.text
      assert simple_query.json()["posts"] == [
        {"id": simple_post_id, "status": "found", "data": simple_post}
      ]

      submitted = client.post(
        "/twitter/x-monkey/api/v1/archive/submit",
        json=native,
        headers=headers,
      )
      assert submitted.status_code == 200, submitted.text
      assert submitted.json()["summary"]["total"] == sum(
        len(native[kind]) for kind in ("users", "posts", "media")
      )

      query = {
        kind: [record["id"] for record in native[kind]]
        for kind in ("users", "posts", "media")
      }
      queried = client.post(
        "/twitter/x-monkey/api/v1/archive/query",
        json=query,
        headers=headers,
      )
      assert queried.status_code == 200, queried.text
      response = queried.json()
      for kind in ("users", "posts", "media"):
        assert [item["status"] for item in response[kind]] == ["found"] * len(native[kind])
        assert [item["data"] for item in response[kind]] == native[kind]
  finally:
    published.unpublish()
    asyncio.run(_cleanup_archive(native_ids))
