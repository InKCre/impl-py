"""Source commands commit independently readable account Bookmark facts.

Real PostgreSQL proves graph identity, rollback and survival after Source deletion.
Only provider I/O and deterministic failure injection are substituted; public graph
navigation is already covered elsewhere and is not part of this writer contract.
"""

from __future__ import annotations

import asyncio
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
  MEDIA_RESOLVER,
  POST_RESOLVER,
  USER_RESOLVER,
  write_twitter,
)
from inkcre_core.business.source import SourceManager
from inkcre_core.persistence.info_base.repository import BlockRepository, RelationRepository
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
  provider = mock.Mock(
    get_bookmarks=mock.AsyncMock(),
    get_authenticated_user_id=mock.AsyncMock(
      side_effect=AssertionError("unexpected identity lookup")
    ),
  )
  # A persistence caller that rereads the client instead of the response is wrong.
  provider.user_id = "999"

  def native(number):
    return str(base + number)

  def post(number, **fields):
    return {
      "id": native(number),
      "authorId": native(20),
      "createdAt": "2026-11-01T00:00:00Z",
      **fields,
    }

  def page(primary, posts, *, actor=40, **records):
    return TwitterAPIResult(
      bookmark_user_id=native(actor) if actor is not None else None,
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

  # Deleting a Source deliberately does not cascade upward to the graph.
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


def roots(*resolvers):
  with TestSession() as db:
    result: dict[str, int] = {}
    rows = db.exec(
      sqlmodel.select(BlockModel).where(
        sqlmodel.col(BlockModel.resolver).in_(
          resolvers or (POST_RESOLVER, LEGACY_POST_RESOLVER)
        )
      )
    ).all()
    for block in rows:
      assert block.id is not None
      native_id = str(json.loads(block.content)["id"])
      assert native_id not in result, "Native identity has duplicate roots"
      result[native_id] = block.id
    return result


def evidence(origin):
  with TestSession() as db:
    rows = db.exec(
      sqlmodel.select(RelationModel).where(RelationModel.from_ == origin)
    ).all()
    result = {(relation.to_, relation.content): relation.id for relation in rows}
    assert len(result) == len(rows), "Evidence contains duplicate relations"
    return result


def assert_no_source_graph(*source_ids):
  with TestSession() as db:
    for source_id in source_ids:
      source = db.get(SourceModel, source_id)
      assert source is not None and source.block is None
    anchors = db.exec(
      sqlmodel.select(BlockModel).where(BlockModel.resolver == "core.source.v1")
    ).all()
    # Also reject orphan anchors that were created without binding sources.block.
    assert not any(json.loads(block.content)["id"] in source_ids for block in anchors)


def test_sources_share_account_bookmarks_without_source_graph(async_runner, journey):
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
  assert_no_source_graph(first, second)
  actor = roots(USER_RESOLVER)[native(40)]
  assert json.loads(block_row(actor).content) == {"id": native(40)}
  ids = roots()
  assert {native(number) for number in (1, 2, 3, 4, 5)} <= ids.keys()
  assert native(6) not in ids
  assert set(evidence(actor)) == {(ids[native(1)], "bookmarks")}
  assert native(30) in roots(MEDIA_RESOLVER)
  # Same actor and Post through another Source retain one domain fact.
  before = evidence(actor)
  collect(second, response)
  collect(first, response, full=True)
  assert_no_source_graph(first, second)
  assert evidence(actor) == before and roots() == ids
  # Ordinary archive submit enriches the identity-only actor but adds no Bookmarks.
  async_runner.run(write_twitter(response.batch))
  async_runner.run(
    write_twitter(
      TwitterBatch.model_validate(
        {"users": [{"id": native(40), "handle": "account_a", "name": "Account A"}]}
      )
    )
  )
  assert roots(USER_RESOLVER)[native(40)] == actor
  assert json.loads(block_row(actor).content)["handle"] == "account_a"
  assert evidence(actor) == before
  collect(first, page([2], [post(2)]), full=True)  # Context later observed as primary.
  assert set(evidence(actor)) == {
    (ids[native(1)], "bookmarks"),
    (ids[native(2)], "bookmarks"),
  }
  async_runner.run(SourceManager.delete(first))
  with TestSession() as db:
    assert db.get(SourceModel, first) is None
  assert len(evidence(actor)) == 2
  assert_no_source_graph(second)


def test_distinct_accounts_share_post_and_keep_historical_bookmarks(async_runner, journey):
  native, post, page, create, collect, provider, _ = journey
  # Account A already has a full profile; account B already has an ID-only root.
  result = async_runner.run(
    write_twitter(
      TwitterBatch.model_validate(
        {"users": [{"id": native(40), "handle": "known", "name": "Known account"}]}
      )
    )
  )
  actor_a = result.block_ids["users"][native(40)]
  actor_b = seed_block(
    BlockForm(resolver=USER_RESOLVER, content=json.dumps({"id": native(41)}))
  )
  assert actor_b.id is not None
  first, second = create(), create()
  collect(first, page([1], [post(1)]))
  collect(second, page([1], [post(1)], actor=41))
  target = roots()[native(1)]
  assert set(evidence(actor_a)) == set(evidence(actor_b.id)) == {(target, "bookmarks")}
  assert roots(USER_RESOLVER)[native(41)] == actor_b.id
  assert json.loads(block_row(actor_a).content)["name"] == "Known account"
  # A later client/global identity must not replace this response's actor.
  provider.user_id = native(42)
  collect(first, page([1], [post(1)], actor=41), full=True)
  assert native(42) not in roots(USER_RESOLVER)
  assert_no_source_graph(first, second)
  assert set(evidence(actor_a)) == set(evidence(actor_b.id)) == {(target, "bookmarks")}


def test_concurrent_sources_converge_on_one_account_bookmark(async_runner, journey):
  native, post, page, create, _, provider, _ = journey
  first, second = create(), create()
  provider.get_bookmarks.return_value = page([1], [post(1)])
  original = BlockRepository.acquire_transaction_lock

  async def exercise():
    owned, attempted, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def coordinated(repository, namespace):
      if owned.is_set():
        attempted.set()
        await original(repository, namespace)
      else:
        await original(repository, namespace)
        owned.set()
        await release.wait()

    async def collect(source_id):
      await bookmark.Source(source_id).collect(
        JobModel(type="core.source.collect.v1"), bookmark.CollectConfig()
      )

    with mock.patch.object(BlockRepository, "acquire_transaction_lock", coordinated):
      a = asyncio.create_task(collect(first))
      await owned.wait()
      b = asyncio.create_task(collect(second))
      await attempted.wait()
      release.set()
      await asyncio.gather(a, b)

  async_runner.run(asyncio.wait_for(exercise(), timeout=10))
  actor = roots(USER_RESOLVER)[native(40)]
  assert set(evidence(actor)) == {(roots()[native(1)], "bookmarks")}
  assert_no_source_graph(first, second)


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
  collect(source_id, page([1], [post(1)]))  # Nothing selected: no graph changes.
  assert_no_source_graph(source_id)
  assert native(40) not in roots(USER_RESOLVER)
  # Marker excluded; skipped primary 2 is captured only as context of primary 3.
  response = page([3, 1, 2], [post(3, quote={"postId": native(2)}), post(1), post(2)])
  collect(source_id, response)
  assert_no_source_graph(source_id)
  actor = roots(USER_RESOLVER)[native(40)]
  ids = roots()
  assert set(evidence(actor)) == {(ids[native(3)], "bookmarks")}
  before = evidence(actor)
  collect(source_id, response)
  collect(source_id, page([], []))
  assert evidence(actor) == before
  provider.get_bookmarks.return_value = response
  job = JobModel(type="core.source.backfill.v1")
  async_runner.run(
    bookmark.Source(source_id).backfill(job, bookmark.BackfillConfig(skip=1, limit=1))
  )
  assert roots()[native(1)] == legacy.id
  assert (legacy.id, "bookmarks") in evidence(actor)
  assert (ids[native(2)], "bookmarks") not in evidence(actor)
  assert (ids[native(7)], "bookmarks") not in evidence(actor)
  assert job.state["saved"] == 1
  assert source_row(source_id).state == {"latest_tweet_id": native(3)}
  collect(source_id, page([2], [post(2)]), full=True)
  assert (ids[native(2)], "bookmarks") in evidence(actor)
  assert source_row(source_id).state == {"latest_tweet_id": native(3)}
  assert_no_source_graph(source_id)
  with TestSession() as db:
    note = db.get(RelationModel, old_note.id)
    assert note is not None and note.content == "bookmarked for"
  assert block_row(ids[native(7)]).resolver == POST_RESOLVER


@pytest.mark.parametrize("command", ["collect", "backfill"])
def test_bookmark_commands_do_not_refresh_or_rewrite_historical_source_graph(
  async_runner, journey, command
):
  native, post, page, create, collect, provider, _ = journey
  history = async_runner.run(
    write_twitter(TwitterBatch.model_validate({"posts": [post(9)]}))
  )
  source_id = create()
  with TestSession() as db:
    source = db.get(SourceModel, source_id)
    assert source is not None
    anchor = seed_block(
      BlockForm(
        resolver="core.source.v1",
        content=json.dumps(
          {"id": source_id, "type": source.type, "nickname": "Stale historical nickname"}
        ),
      ),
      db,
    )
    old_actor = seed_block(
      BlockForm(resolver=USER_RESOLVER, content=json.dumps({"id": native(41)})), db
    )
    assert anchor.id is not None and old_actor.id is not None
    source.block = anchor.id
    db.add(source)
    seed_relation(anchor.id, history.block_ids["posts"][native(9)], "bookmarks", db)
    seed_relation(anchor.id, history.block_ids["users"][native(20)], "collects", db)
    seed_relation(anchor.id, old_actor.id, "collects", db)
    db.commit()
  before_anchor = block_row(anchor.id).model_dump()
  before_relations = evidence(anchor.id)
  response = page([1], [post(1)])
  if command == "collect":
    collect(source_id, response)
  else:
    provider.get_bookmarks.return_value = response
    async_runner.run(
      bookmark.Source(source_id).backfill(
        JobModel(type="core.source.backfill.v1"), bookmark.BackfillConfig(limit=1)
      )
    )
  actor = roots(USER_RESOLVER)[native(40)]
  assert set(evidence(actor)) == {(roots()[native(1)], "bookmarks")}
  # Historical Source edges prove no account membership.
  assert evidence(old_actor.id) == {}
  assert source_row(source_id).block == anchor.id
  assert block_row(anchor.id).model_dump() == before_anchor
  assert evidence(anchor.id) == before_relations
  with TestSession() as db:
    anchors = db.exec(
      sqlmodel.select(BlockModel).where(BlockModel.resolver == "core.source.v1")
    ).all()
    assert [
      block.id for block in anchors if json.loads(block.content)["id"] == source_id
    ] == [anchor.id]
  async_runner.run(SourceManager.delete(source_id))
  assert block_row(anchor.id).model_dump() == before_anchor
  assert evidence(anchor.id) == before_relations
  assert set(evidence(actor)) == {(roots()[native(1)], "bookmarks")}


@pytest.mark.parametrize("failure", ["relations", "marker"])
def test_failed_collection_rolls_back_actor_archive_evidence_and_cursor(
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
  assert_no_source_graph(source_id)
  assert not stored.state
  assert native(1) not in roots() and native(2) not in roots()
  assert native(40) not in roots(USER_RESOLVER)
  collect(source_id, response)
  stored = source_row(source_id)
  assert_no_source_graph(source_id)
  assert stored.state == {"latest_tweet_id": native(1)}
  actor = roots(USER_RESOLVER)[native(40)]
  assert set(evidence(actor)) == {(roots()[native(1)], "bookmarks")}


def test_backfill_page_commits_and_replay_after_failure_without_source_graph(
  async_runner, journey
):
  native, post, page, create, _, provider, _ = journey
  source_id = create()
  first = page([1], [post(1)])
  first.next_page = "second"
  provider.get_bookmarks.side_effect = [first, page([2], [post(2)], actor=41)]
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
  assert_no_source_graph(source_id)
  assert not stored.state
  actor = roots(USER_RESOLVER)[native(40)]
  assert set(evidence(actor)) == {(roots()[native(1)], "bookmarks")}
  assert native(2) not in roots() and native(41) not in roots(USER_RESOLVER)
  assert job.state["saved"] == 1 and job.state["next_page"] == "second"
  # An explicit retry traverses both pages, deduplicating the committed first page.
  before = evidence(actor)
  provider.get_bookmarks.side_effect = [first, page([2], [post(2)], actor=41)]
  retry = JobModel(type="core.source.backfill.v1")
  async_runner.run(
    bookmark.Source(source_id).backfill(retry, bookmark.BackfillConfig(limit=2))
  )
  assert_no_source_graph(source_id)
  assert not source_row(source_id).state
  assert evidence(actor) == before
  second_actor = roots(USER_RESOLVER)[native(41)]
  assert set(evidence(second_actor)) == {(roots()[native(2)], "bookmarks")}
  assert retry.state["saved"] == 2 and retry.state["exhausted"] is True


@pytest.mark.parametrize(
  "command,config,primary",
  [
    ("collect", bookmark.CollectConfig(), []),
    ("collect", bookmark.CollectConfig(full=True), []),
    ("collect", bookmark.CollectConfig(), [1]),
    ("backfill", bookmark.BackfillConfig(limit=1), []),
    ("backfill", bookmark.BackfillConfig(skip=1, limit=1), [1]),
  ],
)
def test_noop_pages_do_not_resolve_actor(async_runner, journey, command, config, primary):
  native, post, page, create, _, provider, _ = journey
  source_id = create()
  marker = {"latest_tweet_id": native(1)}
  async_runner.run(bookmark.Source(source_id).set_state(marker))
  provider.get_bookmarks.return_value = page(
    primary, [post(number) for number in primary], actor=None
  )
  job = JobModel(type=f"core.source.{command}.v1")
  async_runner.run(getattr(bookmark.Source(source_id), command)(job, config))
  provider.get_authenticated_user_id.assert_not_awaited()
  assert source_row(source_id).state == marker
  assert_no_source_graph(source_id)
  assert native(1) not in roots() and native(40) not in roots(USER_RESOLVER)
  if command == "backfill":
    assert job.state["scanned"] == len(primary)
    assert job.state["saved"] == 0 and job.state["exhausted"] is True


@pytest.mark.parametrize("command", ["collect", "backfill"])
def test_selected_page_resolves_fetch_account_before_atomic_write(
  async_runner, journey, command
):
  native, post, page, create, _, provider, _ = journey
  source_id = create()
  provider.get_bookmarks.return_value = page([1], [post(1)], actor=None)
  config = (
    bookmark.CollectConfig() if command == "collect" else bookmark.BackfillConfig(limit=1)
  )

  def run():
    async_runner.run(
      getattr(bookmark.Source(source_id), command)(
        JobModel(type=f"core.source.{command}.v1"), config
      )
    )

  provider.get_authenticated_user_id.side_effect = RuntimeError("identity unavailable")
  with pytest.raises(RuntimeError, match="identity unavailable"):
    run()
  assert not source_row(source_id).state
  assert_no_source_graph(source_id)
  assert native(1) not in roots() and native(40) not in roots(USER_RESOLVER)
  provider.get_authenticated_user_id.side_effect = None
  provider.get_authenticated_user_id.return_value = native(40)
  run()
  actor = roots(USER_RESOLVER)[native(40)]
  assert set(evidence(actor)) == {(roots()[native(1)], "bookmarks")}
  assert_no_source_graph(source_id)
  assert source_row(source_id).state == (
    {"latest_tweet_id": native(1)} if command == "collect" else {}
  )


@pytest.mark.parametrize("marker", [1, 2])
def test_collect_reselects_after_identity_lookup_releases_source_lock(
  async_runner, journey, marker
):
  native, post, page, create, _, provider, _ = journey
  source_id = create()
  provider.get_bookmarks.return_value = page([1, 2], [post(1), post(2)], actor=None)

  async def exercise():
    resolving, advanced = asyncio.Event(), asyncio.Event()

    async def resolve():
      resolving.set()
      await advanced.wait()
      return native(40)

    provider.get_authenticated_user_id.side_effect = resolve
    task = asyncio.create_task(
      bookmark.Source(source_id).collect(
        JobModel(type="core.source.collect.v1"), bookmark.CollectConfig()
      )
    )
    try:
      await resolving.wait()
      # A separate transaction must acquire this same Source's row lock while
      # provider identity I/O is pending, and its new marker governs the write.
      await bookmark.Source(source_id).set_state({"latest_tweet_id": native(marker)})
      advanced.set()
      await task
    finally:
      if not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

  async_runner.run(asyncio.wait_for(exercise(), timeout=10))
  provider.get_authenticated_user_id.assert_awaited_once()
  assert source_row(source_id).state == {"latest_tweet_id": native(1)}
  assert native(2) not in roots()
  if marker == 1:
    assert native(1) not in roots() and native(40) not in roots(USER_RESOLVER)
  else:
    actor = roots(USER_RESOLVER)[native(40)]
    assert set(evidence(actor)) == {(roots()[native(1)], "bookmarks")}
  assert_no_source_graph(source_id)
