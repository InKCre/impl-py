"""Source commands commit independently readable Bookmark and provenance facts.

Real PostgreSQL is necessary: mocks cannot prove graph identity, transaction
rollback or the survival of provenance after operational Source deletion. Only
provider I/O and deterministic failure injection are substituted.
"""

from __future__ import annotations

import json
import os
from unittest import mock
import uuid

import pytest
import sqlalchemy
import sqlmodel

from extensions.twitter import bookmark
from extensions.twitter.api import TwitterAPIResult
from extensions.twitter.model import TwitterBatch
from extensions.twitter.reconcile import (
  LEGACY_POST_RESOLVER,
  POST_RESOLVER,
  write_twitter,
)
from inkcre_core.business.source import SourceManager
from inkcre_core.persistence.info_base.repository import RelationRepository
from inkcre_core.persistence.source.repository import SourceRepository
from inkcre_core.schemas.info_base.block import BlockForm, BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.job import JobModel
from inkcre_core.schemas.source import SourceModel
from tests.database import TestSession, seed_block, seed_relation


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated disposable PostgreSQL runtime",
)


@pytest.fixture
def journey(async_runner):
  source_type = f"{bookmark.Source.__module__}.{bookmark.Source.__qualname__}"
  async_runner.run(SourceManager.sync_source_types_async({source_type: bookmark.Source}))
  base = int(uuid.uuid4().hex[:15], 16) + 10**20
  sources: list[int] = []
  extra_blocks: set[int] = set()
  provider = mock.Mock(get_bookmarks=mock.AsyncMock())

  def native(number):
    return str(base + number)

  def post(number, **fields):
    return {
      "id": native(number),
      "authorId": native(20),
      "createdAt": "2026-11-01T00:00:00Z",
      **fields,
    }

  def page(primary, posts, **records):
    return TwitterAPIResult(
      primary_post_ids=tuple(native(number) for number in primary),
      batch=TwitterBatch.model_validate({"posts": posts, **records}),
    )

  def create():
    source = async_runner.run(SourceManager.create(source_type, nickname="Bookmark test"))
    assert source.id is not None
    sources.append(source.id)
    return source.id

  def collect(source_id, response, *, full=False):
    provider.get_bookmarks.return_value = response
    async_runner.run(
      bookmark.Source(source_id).collect(
        JobModel(type="core.source.collect.v1"), bookmark.CollectConfig(full=full)
      )
    )

  with mock.patch.object(bookmark.TwitterAPI, "new", mock.AsyncMock(return_value=provider)):
    yield native, post, page, create, collect, provider, extra_blocks

  # Source deletion deliberately does not cascade upward to the graph. Clean
  # only this journey's exact rows, including anchors left by deleted Sources.
  with TestSession() as db:
    db.connection().execute(
      sqlalchemy.delete(SourceModel).where(sqlmodel.col(SourceModel.id).in_(sources))
    )
    for block in db.exec(sqlmodel.select(BlockModel)).all():
      try:
        content = json.loads(block.content)
      except ValueError:
        continue
      if isinstance(content, dict) and (
        str(content.get("id")) in {native(number) for number in range(100)}
        or (block.resolver == "core.source.v1" and content.get("id") in sources)
      ):
        assert block.id is not None
        extra_blocks.add(block.id)
    db.connection().execute(
      sqlalchemy.delete(BlockModel).where(sqlmodel.col(BlockModel.id).in_(extra_blocks))
    )
    db.commit()


def source_row(source_id):
  with TestSession() as db:
    source = db.get(SourceModel, source_id)
    assert source is not None
    return source


def block_row(block_id):
  with TestSession() as db:
    block = db.get(BlockModel, block_id)
    assert block is not None
    return block


def roots():
  with TestSession() as db:
    result: dict[str, int] = {}
    for block in db.exec(
      sqlmodel.select(BlockModel).where(
        sqlmodel.col(BlockModel.resolver).in_((POST_RESOLVER, LEGACY_POST_RESOLVER))
      )
    ).all():
      assert block.id is not None
      result[str(json.loads(block.content)["id"])] = block.id
    return result


def evidence(anchor):
  with TestSession() as db:
    rows = db.exec(
      sqlmodel.select(RelationModel).where(RelationModel.from_ == anchor)
    ).all()
    result = {(relation.to_, relation.content): relation.id for relation in rows}
    assert len(result) == len(rows), "Source evidence contains duplicate relations"
    return result


def test_two_sources_share_roots_not_bookmark_membership(async_runner, journey):
  native, post, page, create, collect, _, _ = journey
  first, second = create(), create()
  response = page(
    [1],
    [
      post(1, quote={"postId": native(2)}, repostOf=native(3)),
      post(2, replyTo={"postId": native(4)}),
      post(3),
      post(4, quote={"postId": native(5)}, mediaIds=[native(30)]),
      post(6),  # Unrelated provider expansion is outside admission.
    ],
    users=[{"id": native(20), "handle": "author", "name": "Author"}],
    media=[{"id": native(30), "type": "photo", "url": "https://example.org/p.jpg"}],
  )
  collect(first, response)
  first_anchor = source_row(first).block
  assert first_anchor is not None
  first_edges = evidence(first_anchor)
  captured = {
    json.loads(block_row(target).content)["id"]
    for target, predicate in first_edges
    if predicate == "collects"
  }
  assert captured == {native(number) for number in (1, 2, 3, 4, 20, 30)}
  ids = roots()
  assert native(6) not in ids
  assert (ids[native(5)], "collects") not in first_edges  # ID-only reference.
  assert {
    (target, predicate) for target, predicate in first_edges if predicate == "bookmarks"
  } == {(ids[native(1)], "bookmarks")}
  collect(second, page([2], [post(2)]))
  second_anchor = source_row(second).block
  assert second_anchor is not None and second_anchor != first_anchor
  assert set(evidence(second_anchor)) == {
    (ids[native(2)], "collects"),
    (ids[native(2)], "bookmarks"),
  }
  collect(first, response, full=True)
  assert evidence(first_anchor) == first_edges  # Replay retains relation identities.
  assert roots() == ids
  async_runner.run(write_twitter(response.batch))  # Native-only writes preserve evidence.
  assert evidence(first_anchor) == first_edges
  collect(first, page([2], [post(2)]), full=True)  # Context may later be a primary.
  assert (ids[native(2)], "bookmarks") in evidence(first_anchor)
  assert (ids[native(1)], "bookmarks") in evidence(first_anchor)
  async_runner.run(SourceManager.delete(first))
  with TestSession() as db:
    assert db.get(SourceModel, first) is None
  assert set(evidence(first_anchor)) == {*first_edges, (ids[native(2)], "bookmarks")}
  assert block_row(first_anchor).resolver == "core.source.v1"


def test_history_requires_selected_reobservation_and_preserves_marker(
  async_runner, journey
):
  native, post, page, create, collect, provider, extra = journey
  legacy = seed_block(
    BlockForm(
      resolver=LEGACY_POST_RESOLVER,
      content=json.dumps({"id": int(native(1)), "text": "historical body"}),
    )
  )
  citation = seed_block(BlockForm(resolver="core.text.v1", content="old bookmark note"))
  assert citation.id is not None and legacy.id is not None
  extra.add(citation.id)
  async_runner.run(
    write_twitter(
      TwitterBatch.model_validate({"posts": [post(2, mediaIds=[native(31)]), post(7)]})
    )
  )
  ids = roots()
  old_note = seed_relation(ids[native(2)], citation.id, "bookmarked for")
  source_id = create()
  async_runner.run(
    bookmark.Source(source_id).set_state({"latest_tweet_id": int(native(1))})
  )
  # Numeric historical marker is excluded along with everything after it;
  # skipped primary 2 is nevertheless actually captured as context of 3.
  response = page([3, 1, 2], [post(3, quote={"postId": native(2)}), post(1), post(2)])
  collect(source_id, response)
  anchor = source_row(source_id).block
  ids = roots()
  assert set(evidence(anchor)) == {
    (ids[native(3)], "collects"),
    (ids[native(3)], "bookmarks"),
    (ids[native(2)], "collects"),
  }
  before = evidence(anchor)
  collect(source_id, response)  # Marker first: no new evidence, no history inference.
  collect(source_id, page([], []))
  assert evidence(anchor) == before
  # Explicit bounded backfill upgrades only the selected legacy primary in place.
  provider.get_bookmarks.return_value = response
  job = JobModel(type="core.source.backfill.v1")
  async_runner.run(
    bookmark.Source(source_id).backfill(job, bookmark.BackfillConfig(skip=1, limit=1))
  )
  assert roots()[native(1)] == legacy.id
  assert (legacy.id, "bookmarks") in evidence(anchor)
  assert (ids[native(2)], "bookmarks") not in evidence(anchor)
  assert (ids[native(7)], "collects") not in evidence(anchor)
  assert job.state["saved"] == 1
  assert source_row(source_id).state == {"latest_tweet_id": native(3)}
  # A full re-observation supplies new evidence, but absent retained media does not.
  collect(source_id, page([2], [post(2)]), full=True)
  assert (ids[native(2)], "bookmarks") in evidence(anchor)
  assert source_row(source_id).state == {"latest_tweet_id": native(3)}
  with TestSession() as db:
    note = db.get(RelationModel, old_note.id)
    assert note is not None and note.content == "bookmarked for"
  for target, _ in evidence(anchor):
    assert json.loads(block_row(target).content)["id"] != native(31)
  assert block_row(ids[native(7)]).resolver == POST_RESOLVER


@pytest.mark.parametrize("failure", ["relations", "marker"])
def test_failed_collection_rolls_back_anchor_archive_evidence_and_cursor(
  async_runner, journey, failure
):
  native, post, page, create, collect, _, _ = journey
  source_id = create()
  response = page([1], [post(1, quote={"postId": native(2)})])
  original_relations = RelationRepository.create_many
  original_save = SourceRepository.save

  async def fail_relations(repository, forms):
    rows = await original_relations(repository, forms)
    if any(row.content == "bookmarks" for row in rows):
      raise RuntimeError("page commit failed")
    return rows

  async def fail_marker(repository, source):
    await original_save(repository, source)
    if source.state and "latest_tweet_id" in source.state:
      raise RuntimeError("page commit failed")

  target, method, replacement = (
    (RelationRepository, "create_many", fail_relations)
    if failure == "relations"
    else (SourceRepository, "save", fail_marker)
  )
  with mock.patch.object(target, method, replacement):
    with pytest.raises(RuntimeError, match="page commit failed"):
      collect(source_id, response)
  stored = source_row(source_id)
  assert stored.block is None and not stored.state
  assert native(1) not in roots() and native(2) not in roots()
  with TestSession() as db:
    assert all(
      json.loads(block.content)["id"] != source_id
      for block in db.exec(
        sqlmodel.select(BlockModel).where(BlockModel.resolver == "core.source.v1")
      ).all()
    )
  collect(source_id, response)
  stored = source_row(source_id)
  assert stored.block is not None and stored.state == {"latest_tweet_id": native(1)}
  assert (roots()[native(1)], "bookmarks") in evidence(stored.block)


def test_backfill_failure_preserves_only_committed_pages(async_runner, journey):
  native, post, page, create, _, provider, _ = journey
  source_id = create()
  first = page([1], [post(1)])
  first.next_page = "second"
  provider.get_bookmarks.side_effect = [first, page([2], [post(2)])]
  original = RelationRepository.create_many
  calls = 0

  async def fail_second_page(repository, forms):
    nonlocal calls
    rows = await original(repository, forms)
    if any(row.content == "bookmarks" for row in rows):
      calls += 1
      if calls == 2:
        raise RuntimeError("second page failed")
    return rows

  job = JobModel(type="core.source.backfill.v1")
  with mock.patch.object(RelationRepository, "create_many", fail_second_page):
    with pytest.raises(RuntimeError, match="second page failed"):
      async_runner.run(
        bookmark.Source(source_id).backfill(job, bookmark.BackfillConfig(limit=2))
      )
  stored = source_row(source_id)
  assert stored.block is not None and not stored.state
  assert set(evidence(stored.block)) == {
    (roots()[native(1)], "collects"),
    (roots()[native(1)], "bookmarks"),
  }
  assert native(2) not in roots()
  assert job.state["saved"] == 1 and job.state["next_page"] == "second"
