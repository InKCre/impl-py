"""Native Twitter facts survive sparse, concurrent and cross-source writes."""

from __future__ import annotations

import asyncio
import json
import os
import typing
from types import SimpleNamespace
from unittest import mock

import pytest
import sqlalchemy
from sqlalchemy import event
from sqlalchemy.orm import Session
import sqlmodel
import twikit
from twikit.utils import Result

from inkcre_core.business.info_base.resolver import ResolverManager
from inkcre_core.business.info_base.storage import HTTPStorage
from inkcre_core.business.lexical_retrieval import LexicalRetrievalManager
from inkcre_core.business.source import SourceManager
from inkcre_core.persistence.info_base.repository import BlockRepository, RelationRepository
from inkcre_core.persistence.info_base.uow import graph_uow
from inkcre_core.schemas.info_base.block import BlockForm, BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.info_base.storage import StorageBlobModel
from inkcre_core.schemas.job import JobModel
from inkcre_core.schemas.lexical_retrieval import (
  BlockLexicalRecordModel,
  LexicalMaintenanceOptions,
)
from inkcre_core.schemas.source import SourceModel
from extensions.twitter.api import (
  TwitterAPI,
  TwitterAPIResult,
  TwikitAPI,
)
from extensions.twitter.archive_schema import ArchiveBatch, ArchiveQuery
from extensions.twitter.model import TwitterBatch
from extensions.twitter.x_monkey import query_archive, submit_archive
from extensions.twitter.bookmark import BackfillConfig, CollectConfig, Source
from extensions.twitter.reconcile import (
  MEDIA_RESOLVER,
  POST_RESOLVER,
  USER_RESOLVER,
)
from extensions.twitter.resolver import TweetResolver
from extensions.twitter.schema import Tweet
from tests.database import TestSession, seed_block, seed_relation


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated disposable PostgreSQL runtime",
)


def _minimal(post: dict) -> dict:
  return {field: post[field] for field in ("id", "authorId", "createdAt")}


def _record(native_id: str, resolver: str = POST_RESOLVER) -> BlockModel:
  with TestSession() as db:
    result = db.exec(
      sqlmodel.select(BlockModel).where(
        BlockModel.resolver == resolver,
        sqlalchemy.cast(BlockModel.content, sqlalchemy.JSON)["id"].as_string() == native_id,
      )
    ).one()
    return result


@pytest.fixture
def metadata_only():
  def reject_materialization(session, _context):
    unexpected = [
      type(row).__name__
      for row in session.new
      if isinstance(row, (StorageBlobModel, JobModel))
    ]
    assert not unexpected, f"metadata traversal inserted {unexpected}"

  event.listen(Session, "after_flush", reject_materialization)
  try:
    yield
  finally:
    event.remove(Session, "after_flush", reject_materialization)


@pytest.mark.usefixtures("metadata_only")
def test_native_roundtrip_projection_and_media_are_lazy(async_runner, native, tracked):
  # Importing the module registers all exact decoders, including legacy v1.
  from extensions.twitter import resolver  # noqa: F401

  batch = ArchiveBatch.model_validate(native)
  query = ArchiveQuery(
    **{
      kind: [record.id for record in getattr(batch, kind)]
      for kind in ("users", "posts", "media")
    }
  )
  with mock.patch.object(
    HTTPStorage, "get_raw_content", new_callable=mock.AsyncMock
  ) as fetch:
    fetch.side_effect = AssertionError("metadata read downloaded external media")
    result = async_runner.run(submit_archive(batch))
    assert result.summary.accepted == 3
    response = async_runner.run(query_archive(query))
    for kind in ("users", "posts", "media"):
      assert [item.status for item in getattr(response, kind)] == ["found"]
    post = _record(batch.posts[0].id)
    text = async_runner.run(ResolverManager.get(post).get_text(materialize_missing=False))
    assert native["posts"][0]["article"]["title"] in text
    assert native["users"][0]["handle"] in text
    assert native["posts"][0]["id"] in text
    report = async_runner.run(
      LexicalRetrievalManager.maintain(
        LexicalMaintenanceOptions(max_records=1000, scan_page_size=50)
      )
    )
    assert report.failed == 0, report.diagnostics
    for query_text in (
      batch.posts[0].id,
      native["users"][0]["handle"],
      native["posts"][0]["text"]["text"],
    ):
      matches = async_runner.run(LexicalRetrievalManager.retrieve_local(query_text)).matches
      assert post.id in {match.block.id for match in matches}
    with TestSession() as db:
      indexed = db.exec(
        sqlmodel.select(BlockLexicalRecordModel.block).where(
          sqlmodel.col(BlockLexicalRecordModel.block).in_(tuple(tracked))
        )
      ).all()
      assert set(indexed) == tracked
    assert fetch.await_count == 0
  original_ids = tracked.copy()
  replay = async_runner.run(submit_archive(batch))
  assert replay.summary.unchanged == 3
  assert tracked == original_ids
  assert _record(batch.posts[0].id).id == post.id
  pure_repost = {
    **_minimal(native["posts"][0]),
    "id": str(int(batch.posts[0].id) + 1000),
    "repostOf": batch.posts[0].id,
  }
  async_runner.run(submit_archive(ArchiveBatch.model_validate({"posts": [pure_repost]})))
  repost_response = async_runner.run(query_archive(ArchiveQuery(posts=[pure_repost["id"]])))
  assert repost_response.posts[0].data is not None
  assert (
    repost_response.posts[0].data.model_dump(mode="json", exclude_unset=True) == pure_repost
  )
  assert batch.posts[0].quote is not None
  missing = async_runner.run(
    query_archive(ArchiveQuery(posts=[batch.posts[0].quote.postId]))
  )
  assert missing.posts[0].status == "missing"
  with TestSession() as db:
    media_id = _record(batch.media[0].id, MEDIA_RESOLVER).id
    relations = db.exec(
      sqlmodel.select(RelationModel).where(
        RelationModel.from_ == media_id, RelationModel.content == "content"
      )
    ).all()
    assert relations == []
    stored = db.exec(
      sqlmodel.select(BlockModel).where(sqlmodel.col(BlockModel.id).in_(tuple(tracked)))
    ).all()
    post_data = native["posts"][0]
    assert all(block.storage is None for block in stored)
    assert sorted(
      (block.resolver, json.loads(block.content)["id"]) for block in stored
    ) == sorted(
      [
        (USER_RESOLVER, post_data["authorId"]),
        (MEDIA_RESOLVER, native["media"][0]["id"]),
        (POST_RESOLVER, post_data["id"]),
        (POST_RESOLVER, post_data["quote"]["postId"]),
        (POST_RESOLVER, post_data["replyTo"]["postId"]),
        (POST_RESOLVER, post_data["repostOf"]),
        (POST_RESOLVER, pure_repost["id"]),
      ]
    )


def test_sparse_counter_history_and_managed_edges(async_runner, native):
  async_runner.run(submit_archive(ArchiveBatch.model_validate(native)))
  post = native["posts"][0]
  root_id = _record(post["id"]).id
  author_id = _record(post["authorId"], USER_RESOLVER).id
  assert root_id is not None and author_id is not None
  baseline = _minimal(post)
  updates = [
    (
      {"counts": {"likes": 100}, "countsObservedAt": {"likes": "2026-10-20T01:00:00Z"}},
      100,
      "2026-10-20T01:00:00Z",
      "accepted",
    ),
    (
      {"counts": {"likes": 90}, "countsObservedAt": {"likes": "2026-10-19T01:00:00Z"}},
      100,
      "2026-10-20T01:00:00Z",
      "unchanged",
    ),
    (
      {
        "counts": {"likes": 100},
        "countsObservedAt": {"likes": "2026-10-20T02:00:00.1200Z"},
      },
      100,
      "2026-10-20T02:00:00.1200Z",
      "accepted",
    ),
    ({"counts": {"likes": 101}}, 101, None, "accepted"),
  ]
  for patch, expected, observed, status in updates:
    response = async_runner.run(
      submit_archive(ArchiveBatch.model_validate({"posts": [{**baseline, **patch}]}))
    )
    assert response.posts[0].status == status
    stored = json.loads(_record(post["id"]).content)
    assert stored["counts"]["likes"] == expected
    assert stored.get("countsObservedAt", {}).get("likes") == observed
    assert stored["text"] == post["text"]
    assert stored["article"] == post["article"]
    assert _record(post["id"]).id == root_id

  # A changed media array replaces its edges without touching user-authored links.
  user_link = seed_relation(root_id, author_id, "my citation")
  old_media_id = post["mediaIds"][0]
  replacement = str(int(old_media_id) + 1000)
  cover = str(int(old_media_id) + 1001)
  embedded = str(int(old_media_id) + 1002)
  update = ArchiveBatch.model_validate(
    {
      "posts": [
        {
          **baseline,
          "mediaIds": [replacement],
          "article": {
            **post["article"],
            "coverMediaId": cover,
            "mediaIds": [embedded, replacement, cover],
          },
        }
      ]
    }
  )
  async_runner.run(submit_archive(update))
  with TestSession() as db:
    assert db.get(RelationModel, user_link.id) is not None
    media_refs = []
    for relation in db.exec(
      sqlmodel.select(RelationModel).where(RelationModel.from_ == root_id)
    ).all():
      try:
        grammar = json.loads(relation.content)
      except ValueError:
        continue
      if (
        grammar.get("schema") == "extensions.twitter.reference.v1"
        and grammar["type"] == "media"
      ):
        target = db.get(BlockModel, relation.to_)
        assert target is not None
        media_refs.append((grammar["position"], json.loads(target.content)["id"]))
    assert sorted(media_refs) == [(0, replacement), (1, cover), (2, embedded)]


def test_bookmark_joins_legacy_and_native_identity_without_overwriting_text(
  async_runner, native
):
  post = native["posts"][0]
  legacy = seed_block(
    TweetResolver.create_block(Tweet(id=int(post["id"]), text="旧加工 [photo]"))
  )
  citation = seed_block(BlockForm(resolver="core.text.v1", content="user citation"))
  assert legacy.id is not None and citation.id is not None
  reference = seed_relation(citation.id, legacy.id, "reference")
  old_attachment = seed_relation(legacy.id, citation.id, "attachment:photo:legacy")
  async_runner.run(submit_archive(ArchiveBatch.model_validate(native)))
  assert _record(post["id"]).id == legacy.id
  with TestSession() as db:
    persisted_reference = db.get(RelationModel, reference.id)
    assert persisted_reference is not None and persisted_reference.to_ == legacy.id
    assert db.get(RelationModel, old_attachment.id) is not None

  source_type = f"{Source.__module__}.{Source.__qualname__}"
  async_runner.run(SourceManager.sync_source_types_async({source_type: Source}))
  sparse = {**_minimal(post), "text": {"text": "provider has less detail"}, "lang": "en"}
  provider = mock.Mock()
  provider.get_bookmarks = mock.AsyncMock(
    return_value=TwitterAPIResult(
      batch=TwitterBatch.model_validate({"posts": [sparse]}), primary_post_ids=(post["id"],)
    )
  )
  source = async_runner.run(
    SourceManager.create(source_type, nickname="test Twitter bookmarks")
  )
  try:
    with mock.patch.object(
      TwitterAPI, "new", new_callable=mock.AsyncMock, return_value=provider
    ):
      async_runner.run(
        Source(source.id).collect(
          JobModel(type="core.source.collect.v1", parameters={}), CollectConfig()
        )
      )
    stored = json.loads(_record(post["id"]).content)
    assert stored["text"] == post["text"]
    assert stored["lang"] == "en"
    with TestSession() as db:
      persisted_source = db.get(SourceModel, source.id)
      assert persisted_source is not None and persisted_source.state is not None
      assert persisted_source.state["latest_tweet_id"] == post["id"]
  finally:
    with TestSession() as db:
      db.delete(db.get(SourceModel, source.id))
      db.commit()


def test_real_transaction_lock_preserves_concurrent_supplements_and_rollback(
  async_runner, native
):
  baseline = _minimal(native["posts"][0])
  original_lock = BlockRepository.acquire_transaction_lock

  async def exercise():
    locked, attempted, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def coordinated(repository, namespace):
      task = asyncio.current_task()
      assert task is not None
      if task.get_name() == "second":
        attempted.set()
      await original_lock(repository, namespace)
      if task.get_name() == "first":
        locked.set()
        await release.wait()

    with mock.patch.object(BlockRepository, "acquire_transaction_lock", coordinated):
      first = asyncio.create_task(
        submit_archive(
          ArchiveBatch.model_validate({"posts": [{**baseline, "source": "client"}]})
        ),
        name="first",
      )
      await locked.wait()
      second = asyncio.create_task(
        submit_archive(
          ArchiveBatch.model_validate({"posts": [{**baseline, "lang": "zh"}]})
        ),
        name="second",
      )
      await attempted.wait()
      release.set()
      await asyncio.gather(first, second)
    async with graph_uow() as uow:
      roots = await uow.blocks.find_json_field_many(
        (POST_RESOLVER, USER_RESOLVER), "id", (baseline["id"], baseline["authorId"])
      )
      posts = [root for root in roots if root.resolver == POST_RESOLVER]
      assert len(posts) == 1
      stored = json.loads(posts[0].content)
      assert stored["source"] == "client" and stored["lang"] == "zh"

    failed: dict[str, typing.Any] = {
      **baseline,
      "id": str(int(baseline["id"]) + 1000),
      "quote": {"postId": str(int(baseline["id"]) + 1001)},
    }
    batch = ArchiveBatch.model_validate({"posts": [failed]})
    with mock.patch.object(
      RelationRepository,
      "create_many",
      new_callable=mock.AsyncMock,
      side_effect=RuntimeError("deterministic persistence failure"),
    ):
      with pytest.raises(RuntimeError, match="deterministic persistence failure"):
        await submit_archive(batch)
    absent = await query_archive(
      ArchiveQuery(posts=[failed["id"], failed["quote"]["postId"]])
    )
    assert [item.status for item in absent.posts] == ["missing", "missing"]
    await submit_archive(batch)
    assert (await query_archive(ArchiveQuery(posts=[failed["id"]]))).posts[
      0
    ].status == "found"

  async_runner.run(exercise())


def test_backfill_preserves_committed_page_when_next_commit_fails(async_runner, native):
  source_type = f"{Source.__module__}.{Source.__qualname__}"
  async_runner.run(SourceManager.sync_source_types_async({source_type: Source}))
  source = async_runner.run(
    SourceManager.create(source_type, nickname="Backfill commit failure")
  )
  first_id = native["posts"][0]["id"]
  second_id = str(int(first_id) + 1000)
  first = _minimal(native["posts"][0])
  async_runner.run(
    submit_archive(
      ArchiveBatch.model_validate(
        {"posts": [{**first, "text": {"text": "已归档的完整原文"}}]}
      )
    )
  )
  with TestSession() as db:
    persisted_source = db.get(SourceModel, source.id)
    assert persisted_source is not None
    persisted_source.state = {"latest_tweet_id": "42"}
    db.add(persisted_source)
    db.commit()

  def page(post_id, cursor):
    return TwitterAPIResult(
      primary_post_ids=(post_id,),
      next_page=cursor,
      batch=TwitterBatch.model_validate(
        {"posts": [{**first, "id": post_id, "text": {"text": "书签预览"}, "lang": "zh"}]}
      ),
    )

  provider = SimpleNamespace(
    get_bookmarks=mock.AsyncMock(side_effect=[page(first_id, "a"), page(second_id, "b")])
  )
  flushed, commit_attempted = [], []

  def remember_second_page(session, _context):
    for row in session.new:
      if isinstance(row, BlockModel) and json.loads(row.content).get("id") == second_id:
        session.info["backfill_second_page"] = True
        flushed.append(row.id)

  def fail_second_commit(session):
    if session.info.get("backfill_second_page"):
      commit_attempted.append(True)
      raise RuntimeError("injected second-page commit failure")

  job = JobModel(type="core.source.backfill.v1", timeout_seconds=60)
  event.listen(Session, "after_flush", remember_second_page)
  event.listen(Session, "before_commit", fail_second_commit)
  try:
    with mock.patch.object(TwitterAPI, "new", mock.AsyncMock(return_value=provider)):
      with pytest.raises(RuntimeError, match="injected second-page commit failure"):
        async_runner.run(
          Source(source.id).backfill(job, BackfillConfig(limit=2, page_size=1))
        )
  finally:
    event.remove(Session, "after_flush", remember_second_page)
    event.remove(Session, "before_commit", fail_second_commit)

    with TestSession() as db:
      current = db.get(SourceModel, source.id)
      assert current is not None
      marker = current.state
      db.delete(current)
      db.commit()
  assert flushed and commit_attempted
  response = async_runner.run(query_archive(ArchiveQuery(posts=[first_id, second_id])))
  assert [item.status for item in response.posts] == ["found", "missing"]
  committed = response.posts[0].data
  assert committed is not None and committed.lang == "zh"
  assert committed.text is not None and committed.text.text == "已归档的完整原文"
  assert marker == {"latest_tweet_id": "42"}
  assert job.state == {
    "scanned": 1,
    "saved": 1,
    "pages": 1,
    "exhausted": False,
    "next_page": "a",
  }


def test_batch_lookup_tolerates_non_json_and_reports_locatable_corruption(
  async_runner, native
):
  batch = ArchiveBatch.model_validate(native)
  async_runner.run(submit_archive(batch))
  unknown = str(int(batch.posts[0].id) + 1000)
  for form in (
    BlockForm(resolver="core.text.v1", content="ordinary non-JSON text"),
    BlockForm(resolver=POST_RESOLVER, content="broken JSON {"),
    BlockForm(
      resolver=POST_RESOLVER,
      content=json.dumps({"id": unknown, "authorId": batch.posts[0].authorId}),
    ),
  ):
    seed_block(form)
  response = async_runner.run(
    query_archive(ArchiveQuery(posts=[batch.posts[0].id, unknown]))
  )
  assert [item.status for item in response.posts] == ["found", "failed"]


def test_media_metadata_roundtrips_without_content_blocks(async_runner, native, tracked):
  base = int(native["media"][0]["id"]) + 1000
  records: list[dict[str, typing.Any]] = [
    {"id": str(base), "type": "photo", "url": "https://example.org/image.jpg"},
    {
      "id": str(base + 1),
      "type": "video",
      "url": "https://pbs.twimg.com/poster.jpg",
      "video": {
        "variants": [
          {"url": "https://video.twimg.com/list.m3u8", "type": "application/x-mpegURL"}
        ]
      },
    },
    {"id": str(base + 2), "type": "photo", "url": "https://pbs.twimg.com/\nimage.jpg"},
    {
      "id": str(base + 3),
      "type": "video",
      "url": "https://pbs.twimg.com/poster.jpg",
      "video": {
        "variants": [
          {
            "url": f"https://video.twimg.com/{base}-low.mp4",
            "type": "video/mp4",
            "bitrate": 1,
          },
          {"url": "https://example.org/foreign.mp4", "type": "video/mp4", "bitrate": 99},
          {
            "url": f"https://video.twimg.com/{base}-high.mp4",
            "type": "video/mp4",
            "bitrate": 2,
          },
        ]
      },
    },
  ]
  async_runner.run(submit_archive(ArchiveBatch.model_validate({"media": records})))
  response = async_runner.run(
    query_archive(ArchiveQuery(media=[record["id"] for record in records]))
  )
  assert [item.status for item in response.media] == ["found"] * 4
  assert [
    item.data.model_dump(mode="json", exclude_unset=True) for item in response.media
  ] == records
  with TestSession() as db:
    content = db.exec(
      sqlmodel.select(RelationModel).where(
        sqlmodel.col(RelationModel.from_).in_(tracked),
        RelationModel.content == "content",
      )
    ).all()
    assert content == []


def test_representative_batch_keeps_database_round_trips_bounded(
  async_runner, native, tracked
):
  from app.engine import ASYNC_DB_ENGINE

  base = int(native["posts"][0]["id"])
  raw: dict[str, list[dict[str, typing.Any]]] = {"users": [], "posts": [], "media": []}
  for index in range(200):
    user, post, media, quoted = (
      str(base + offset + index) for offset in (0, 1000, 2000, 3000)
    )
    raw["users"].append({"id": user, "handle": f"author{index}", "name": f"Author {index}"})
    raw["posts"].append(
      {
        "id": post,
        "authorId": user,
        "createdAt": "2026-10-19T01:00:00Z",
        "text": {"text": "benchmark " + "正文 " * 40},
        "mediaIds": [media],
        "quote": {"postId": quoted},
      }
    )
    raw["media"].append(
      {
        "id": media,
        "type": "photo",
        "url": f"https://pbs.twimg.com/batch-{base}-{index}.jpg",
      }
    )
  calls: list[str] = []

  def count_round_trip(_connection, _cursor, statement, _parameters, _context, _many):
    calls.append(statement.split(None, 1)[0])

  event.listen(ASYNC_DB_ENGINE.sync_engine, "before_cursor_execute", count_round_trip)
  try:
    result = async_runner.run(submit_archive(ArchiveBatch.model_validate(raw)))
  finally:
    event.remove(ASYNC_DB_ENGINE.sync_engine, "before_cursor_execute", count_round_trip)
  assert result.summary.accepted == 600
  # Keep the synchronous native batch bounded when Core and DB are remote.
  assert len(calls) < 20, {kind: calls.count(kind) for kind in set(calls)}
  with TestSession() as db:
    by_resolver = dict(
      db.exec(
        sqlmodel.select(BlockModel.resolver, sqlmodel.func.count())
        .where(sqlmodel.col(BlockModel.id).in_(tracked))
        .group_by(BlockModel.resolver)
      ).all()
    )
    assert by_resolver == {POST_RESOLVER: 400, USER_RESOLVER: 200, MEDIA_RESOLVER: 200}


def test_twikit_bookmark_first_then_native_enrichment_keeps_one_root(async_runner, native):
  post = native["posts"][0]
  media_id = native["media"][0]["id"]
  author = SimpleNamespace(id=post["authorId"], screen_name="source_handle", name="source")
  raw = {
    "rest_id": post["id"],
    "legacy": {
      "created_at": "Sun Oct 18 12:34:56 +0000 2026",
      "full_text": "truncated legacy…",
      "lang": "zh",
      "conversation_id_str": str(int(post["id"]) - 1),
      "in_reply_to_status_id_str": str(int(post["id"]) - 2),
      "entities": {
        "media": [
          {
            "id_str": media_id,
            "type": "photo",
            "media_url_https": native["media"][0]["url"],
            "url": "https://t.co/source",
            "original_info": {"width": 1920, "height": 1080},
          }
        ],
        "urls": [],
      },
    },
    "note_tweet": {
      "note_tweet_results": {
        "result": {
          "text": "@mention 完整🌏 &amp; https://t.co/source",
        }
      }
    },
  }
  # Exercise the installed provider's real getters, including note-tweet selection.
  tweet = twikit.Tweet(mock.Mock(), raw, typing.cast(twikit.User, author))
  source_type = f"{Source.__module__}.{Source.__qualname__}"
  async_runner.run(SourceManager.sync_source_types_async({source_type: Source}))
  source = async_runner.run(SourceManager.create(source_type, nickname="Twikit first"))
  sdk = SimpleNamespace(
    _user_id=author.id, get_bookmarks=mock.AsyncMock(return_value=Result([tweet]))
  )
  with mock.patch.object(twikit, "Client", return_value=sdk):
    provider = TwikitAPI("fixture@example.invalid", "collector", "fixture-password")
  try:
    with (
      mock.patch.object(
        TwitterAPI, "new", new_callable=mock.AsyncMock, return_value=provider
      ),
      mock.patch.object(
        HTTPStorage,
        "get_raw_content",
        new_callable=mock.AsyncMock,
        side_effect=AssertionError("Bookmark indexing downloaded external media"),
      ) as fetch,
    ):
      async_runner.run(
        Source(source.id).collect(
          JobModel(type="core.source.collect.v1", parameters={}), CollectConfig()
        )
      )
      root = _record(post["id"])
      stored = json.loads(root.content)
      assert stored["authorId"] == author.id
      assert stored["conversationId"] == raw["legacy"]["conversation_id_str"]
      assert stored["replyTo"]["postId"] == raw["legacy"]["in_reply_to_status_id_str"]
      assert stored["text"]["text"] == "@mention 完整🌏 & https://t.co/source"
      assert stored["mediaIds"] == [media_id]
      report = async_runner.run(
        LexicalRetrievalManager.maintain(
          LexicalMaintenanceOptions(max_records=1000, scan_page_size=50)
        )
      )
      assert report.failed == 0, report.diagnostics
      matches = async_runner.run(LexicalRetrievalManager.retrieve_local(post["id"])).matches
      assert root.id in {match.block.id for match in matches}
      assert fetch.await_count == 0
  finally:
    with TestSession() as db:
      db.delete(db.get(SourceModel, source.id))
      db.commit()
  async_runner.run(submit_archive(ArchiveBatch.model_validate(native)))
  assert _record(post["id"]).id == root.id
  assert json.loads(_record(post["id"]).content)["text"] == post["text"]
