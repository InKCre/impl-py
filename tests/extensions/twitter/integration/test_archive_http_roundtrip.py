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


def _archive_owned_ids(
  native: dict,
  *,
  preexisting_ids: dict[str, set[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
  """Return submitted identities and only the absent references this batch creates."""
  resolvers = {"users": USER_RESOLVER, "posts": POST_RESOLVER, "media": MEDIA_RESOLVER}
  defined = {
    kind: {record["id"] for record in native[kind]} for kind in ("users", "posts", "media")
  }
  referenced = {kind: set() for kind in ("users", "posts", "media")}
  for post in native["posts"]:
    if "authorId" in post:
      referenced["users"].add(post["authorId"])
    article = post.get("article") or {}
    media_ids = list(post.get("mediaIds", ()))
    if "coverMediaId" in article:
      media_ids.append(article["coverMediaId"])
    media_ids.extend(article.get("mediaIds", ()))
    referenced["media"].update(media_ids)
    for field in ("replyTo", "quote"):
      if field in post:
        referenced["posts"].add(post[field]["postId"])
    if "repostOf" in post:
      referenced["posts"].add(post["repostOf"])

  existing = preexisting_ids or {}
  return {
    resolvers[kind]: tuple(
      sorted((defined[kind] | referenced[kind]) - existing.get(resolvers[kind], set()))
    )
    for kind in ("users", "posts", "media")
  }


async def _cleanup_archive(owned_ids: dict[str, tuple[str, ...]]) -> None:
  async with graph_uow() as uow:
    roots = []
    for resolver, native_ids in owned_ids.items():
      if native_ids:
        roots.extend(await uow.blocks.find_json_field_many((resolver,), "id", native_ids))
    tracked_ids = {block.id for block in roots if block.id is not None}

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
  async with graph_uow() as uow:
    remaining = []
    for resolver, native_ids in owned_ids.items():
      if native_ids:
        remaining.extend(
          await uow.blocks.find_json_field_many((resolver,), "id", native_ids)
        )
  if remaining:
    raise AssertionError("cleanup left test-owned archive identities in PostgreSQL")


def test_real_http_submit_query_preserves_native_wire_payload(monkeypatch):
  from extensions.twitter import Extension

  native = _distinct_fixture()
  simple_post_id = str(int(uuid.uuid4().hex[:15], 16) + 10**30)
  shared_author_id = str(int(simple_post_id) + 1)
  owned_ids = _archive_owned_ids(
    native,
    preexisting_ids={USER_RESOLVER: {shared_author_id}},
  )
  owned_ids[POST_RESOLVER] = tuple(sorted((*owned_ids[POST_RESOLVER], simple_post_id)))
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
  shared_author_survived_cleanup = False
  try:
    with published.client as client:
      client.cookies.set(SESSION_COOKIE_NAME, token)
      headers = {"Origin": "https://x.com", "X-Tweet-DB-Client": "x-monkey"}
      simple_post = {
        "id": simple_post_id,
        "authorId": shared_author_id,
        "createdAt": "2026-10-19T12:00:00Z",
        "text": {"text": "HTTP to PostgreSQL round trip"},
      }
      simple_submit = client.post(
        "/twitter/x-monkey/api/v1/archive/submit",
        json={"posts": [simple_post]},
        headers=headers,
      )
      assert simple_submit.status_code == 200, simple_submit.text
      with TestSession() as db:
        simple_root = next(
          block
          for block in db.exec(
            sqlmodel.select(BlockModel).where(BlockModel.resolver == POST_RESOLVER)
          ).all()
          if json.loads(block.content).get("id") == simple_post_id
        )
        assert simple_root.id is not None
        assert (
          db.exec(
            sqlmodel.select(RelationModel).where(
              sqlmodel.col(RelationModel.from_) == simple_root.id,
              sqlmodel.col(RelationModel.to_) == shared_author_block_id,
            )
          ).first()
          is not None
        )
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
    asyncio.run(_cleanup_archive(owned_ids))
    with TestSession() as db:
      shared_author_after_cleanup = db.get(BlockModel, shared_author_block_id)
      shared_author_survived_cleanup = shared_author_after_cleanup is not None
      if shared_author_after_cleanup is not None:
        db.delete(shared_author_after_cleanup)
      db.commit()
  assert shared_author_survived_cleanup, "cleanup removed a preexisting author identity"
