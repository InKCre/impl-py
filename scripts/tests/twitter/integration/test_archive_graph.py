"""Native Twitter facts survive sparse, concurrent and cross-source writes."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import typing
from types import SimpleNamespace
from unittest import mock
import uuid

import pytest
import sqlalchemy
from sqlalchemy import event
import sqlmodel
import twikit

from app.business.info_base.resolver import ResolverManager
from app.business.info_base.storage import HTTPStorage
from app.business.lexical_retrieval import LexicalRetrievalManager
from app.business.source import SourceManager
from app.persistence.info_base.repository import BlockRepository, RelationRepository
from app.persistence.info_base.uow import graph_uow
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.info_base.relation import RelationModel
from app.schemas.info_base.storage import StorageBlobModel
from app.schemas.job import JobModel
from app.schemas.lexical_retrieval import (
  BlockLexicalRecordModel,
  LexicalMaintenanceOptions,
)
from app.schemas.source import SourceModel
from extensions.twitter.api import (
  TwitterAPI,
  TwitterAPIResult,
  _official_archive,
  _twikit_archive,
)
from extensions.twitter.archive_schema import ArchiveBatch, ArchiveQuery
from extensions.twitter.model import TwitterBatch
from extensions.twitter.x_monkey import (
  decode_submission,
  encode_submission,
  query_archive,
  submit_archive,
)
from extensions.twitter.bookmark import CollectConfig, Source
from extensions.twitter.reconcile import (
  MEDIA_RESOLVER,
  POST_RESOLVER,
  REFERENCE_SCHEMA,
  USER_RESOLVER,
  persist_twitter,
)
from extensions.twitter.resolver import TweetResolver
from extensions.twitter.schema import Tweet
from tests.database import TestSession, seed_block, seed_relation


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated disposable PostgreSQL runtime",
)


@pytest.fixture
def native() -> dict[str, typing.Any]:
  value = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "native_archive.json").read_text()
  )
  offset = int(uuid.uuid4().hex[:15], 16) + 10**20

  def distinct(item):
    if isinstance(item, str):
      if item.isascii() and item.isdecimal():
        return str(int(item) + offset)
      if item.startswith(("https://pbs.twimg.com/", "https://video.twimg.com/")):
        return f"{item}{'&' if '?' in item else '?'}twitter_test={offset}"
    if isinstance(item, list):
      return [distinct(child) for child in item]
    if isinstance(item, dict):
      return {key: distinct(child) for key, child in item.items()}
    return item

  return distinct(value)


@pytest.fixture
def tracked() -> typing.Iterator[set[int]]:
  ids: set[int] = set()
  yield ids
  if ids:
    with TestSession() as db:
      db.connection().execute(
        sqlalchemy.delete(BlockModel).where(sqlmodel.col(BlockModel.id).in_(tuple(ids)))
      )
      db.commit()


async def _write(batch: ArchiveBatch | TwitterBatch, tracked: set[int]):
  async with graph_uow() as uow:
    result = await persist_twitter(
      decode_submission(batch) if isinstance(batch, ArchiveBatch) else batch, uow
    )
  tracked.update(block_id for ids in result.block_ids.values() for block_id in ids.values())
  return SimpleNamespace(
    block_ids=result.block_ids,
    response=encode_submission(
      batch
      if isinstance(batch, ArchiveBatch)
      else ArchiveBatch.model_validate(batch.model_dump(mode="json", exclude_unset=True)),
      result,
    ),
  )


def _minimal(post: dict) -> dict:
  return {field: post[field] for field in ("id", "authorId", "createdAt")}


def _record(native_id: str) -> BlockModel:
  with TestSession() as db:
    result = db.exec(
      sqlmodel.select(BlockModel).where(
        BlockModel.resolver == POST_RESOLVER,
        sqlalchemy.cast(BlockModel.content, sqlalchemy.JSON)["id"].as_string() == native_id,
      )
    ).one()
    return result


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
  with TestSession() as db:
    block_count = db.exec(
      sqlmodel.select(sqlmodel.func.count()).select_from(BlockModel)
    ).one()
    blob_count = db.exec(
      sqlmodel.select(sqlmodel.func.count()).select_from(StorageBlobModel)
    ).one()
    job_count = db.exec(sqlmodel.select(sqlmodel.func.count()).select_from(JobModel)).one()
  with mock.patch.object(
    HTTPStorage, "get_raw_content", new_callable=mock.AsyncMock
  ) as fetch:
    fetch.side_effect = AssertionError("metadata read downloaded external media")
    result = async_runner.run(_write(batch, tracked))
    assert result.response.summary.accepted == 3
    response = async_runner.run(query_archive(query))
    for kind in ("users", "posts", "media"):
      assert [item.status for item in getattr(response, kind)] == ["found"]
      assert [
        item.data.model_dump(mode="json", exclude_unset=True)
        for item in getattr(response, kind)
      ] == native[kind]
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
  replay = async_runner.run(_write(batch, tracked))
  assert replay.response.summary.unchanged == 3
  assert replay.block_ids == result.block_ids
  pure_repost = {
    **_minimal(native["posts"][0]),
    "id": str(int(batch.posts[0].id) + 1000),
    "repostOf": batch.posts[0].id,
  }
  async_runner.run(_write(ArchiveBatch.model_validate({"posts": [pure_repost]}), tracked))
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
    assert (
      db.exec(sqlmodel.select(sqlmodel.func.count()).select_from(StorageBlobModel)).one()
      == blob_count
    )
    assert (
      db.exec(sqlmodel.select(sqlmodel.func.count()).select_from(JobModel)).one()
      == job_count
    )
    media_id = result.block_ids["media"][batch.media[0].id]
    relations = db.exec(
      sqlmodel.select(RelationModel).where(
        RelationModel.from_ == media_id, RelationModel.content == "content"
      )
    ).all()
    assert relations == []
    stored = db.exec(
      sqlmodel.select(BlockModel).where(sqlmodel.col(BlockModel.id).in_(tuple(tracked)))
    ).all()
    assert len(stored) == len(tracked)
    assert all(block.storage is None for block in stored)
    assert db.exec(
      sqlmodel.select(sqlmodel.func.count()).select_from(BlockModel)
    ).one() == block_count + len(tracked)


def test_sparse_counter_history_and_managed_edges(async_runner, native, tracked):
  result = async_runner.run(_write(ArchiveBatch.model_validate(native), tracked))
  post = native["posts"][0]
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
    assert _record(post["id"]).id == result.block_ids["posts"][post["id"]]

  # A changed media array replaces its edges without touching user-authored links.
  root_id = result.block_ids["posts"][post["id"]]
  user_link = seed_relation(
    root_id, result.block_ids["users"][post["authorId"]], "my citation"
  )
  old_media_id = post["mediaIds"][0]
  replacement = str(int(old_media_id) + 1000)
  update = ArchiveBatch.model_validate({"posts": [{**baseline, "mediaIds": [replacement]}]})
  async_runner.run(_write(update, tracked))
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
      if grammar.get("schema") == REFERENCE_SCHEMA and grammar["type"] == "media":
        target = db.get(BlockModel, relation.to_)
        assert target is not None
        media_refs.append((grammar["position"], json.loads(target.content)["id"]))
    expected = list(
      dict.fromkeys(
        [replacement, post["article"]["coverMediaId"], *post["article"]["mediaIds"]]
      )
    )
    assert sorted(media_refs) == list(enumerate(expected))


def test_bookmark_joins_legacy_and_native_identity_without_overwriting_text(
  async_runner, native, tracked
):
  post = native["posts"][0]
  legacy = seed_block(
    TweetResolver.create_block(Tweet(id=int(post["id"]), text="旧加工 [photo]"))
  )
  citation = seed_block(BlockForm(resolver="core.text.v1", content="user citation"))
  assert legacy.id is not None and citation.id is not None
  tracked.update((legacy.id, citation.id))
  reference = seed_relation(citation.id, legacy.id, "reference")
  old_attachment = seed_relation(legacy.id, citation.id, "attachment:photo:legacy")
  result = async_runner.run(_write(ArchiveBatch.model_validate(native), tracked))
  assert result.block_ids["posts"][post["id"]] == legacy.id
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
  async_runner, native, tracked
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
      tracked.update(block.id for block in roots)
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
    await _write(batch, tracked)
    assert (await query_archive(ArchiveQuery(posts=[failed["id"]]))).posts[
      0
    ].status == "found"

  async_runner.run(exercise())


def test_batch_lookup_tolerates_non_json_and_reports_locatable_corruption(
  async_runner, native, tracked
):
  batch = ArchiveBatch.model_validate(native)
  async_runner.run(_write(batch, tracked))
  unknown = str(int(batch.posts[0].id) + 1000)
  broken = [
    seed_block(BlockForm(resolver="core.text.v1", content="ordinary non-JSON text")),
    seed_block(BlockForm(resolver=POST_RESOLVER, content="broken JSON {")),
    seed_block(
      BlockForm(
        resolver=POST_RESOLVER,
        content=json.dumps({"id": unknown, "authorId": batch.posts[0].authorId}),
      )
    ),
  ]
  tracked.update(block.id for block in broken)
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
  result = async_runner.run(
    _write(ArchiveBatch.model_validate({"media": records}), tracked)
  )
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
        sqlmodel.col(RelationModel.from_).in_(tuple(result.block_ids["media"].values())),
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
    result = async_runner.run(_write(ArchiveBatch.model_validate(raw), tracked))
  finally:
    event.remove(ASYNC_DB_ENGINE.sync_engine, "before_cursor_execute", count_round_trip)
  assert result.response.summary.accepted == 600
  # Keep the synchronous native batch bounded when Core and DB are remote.
  assert len(calls) < 20, {kind: calls.count(kind) for kind in set(calls)}
  assert len(result.block_ids["posts"]) == 400
  with TestSession() as db:
    contents = db.exec(
      sqlmodel.select(RelationModel).where(
        sqlmodel.col(RelationModel.from_).in_(tuple(result.block_ids["media"].values())),
        RelationModel.content == "content",
      )
    ).all()
    assert contents == []


def test_official_provider_keeps_source_text_and_exact_id_semantics(
  async_runner, native, tracked
):
  root = native["posts"][0]
  batch = _official_archive(
    [
      {
        "id": root["id"],
        "author_id": root["authorId"],
        "created_at": root["createdAt"],
        "text": "@reply 原始🌏 &amp; https://t.co/media",
        "conversation_id": "1",
        "referenced_tweets": [{"type": "replied_to", "id": "2"}],
      }
    ],
    {},
    "2026-10-19T01:00:00Z",
  )
  assert batch.posts[0].text is not None
  assert batch.posts[0].text.text == "@reply 原始🌏 & https://t.co/media"
  assert batch.posts[0].conversationId == "1"
  assert batch.posts[0].replyTo is not None and batch.posts[0].replyTo.postId == "2"
  result = async_runner.run(_write(batch, tracked))
  assert result.response.posts[0].status == "accepted"


def test_twikit_bookmark_first_then_native_enrichment_keeps_one_root(
  async_runner, native, tracked
):
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
  batch = _twikit_archive((tweet,), "2026-10-19T01:00:00Z")
  assert batch.posts[0].authorId == author.id
  assert batch.posts[0].replyTo is not None and batch.posts[0].text is not None
  assert batch.posts[0].conversationId != batch.posts[0].replyTo.postId
  assert batch.posts[0].text.text == "@mention 完整🌏 & https://t.co/source"
  assert [media.id for media in batch.media] == [media_id]
  source_type = f"{Source.__module__}.{Source.__qualname__}"
  async_runner.run(SourceManager.sync_source_types_async({source_type: Source}))
  source = async_runner.run(SourceManager.create(source_type, nickname="Twikit first"))
  provider = mock.Mock()
  provider.get_bookmarks = mock.AsyncMock(
    return_value=TwitterAPIResult(batch=batch, primary_post_ids=(post["id"],))
  )
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
    # Track native identities as well as the root created inside Source's UoW.
    async def remember():
      async with graph_uow() as uow:
        rows = await uow.blocks.find_json_field_many(
          (POST_RESOLVER, USER_RESOLVER, MEDIA_RESOLVER),
          "id",
          (post["id"], author.id, raw["legacy"]["in_reply_to_status_id_str"], media_id),
        )
        tracked.update(row.id for row in rows)

    async_runner.run(remember())
    with TestSession() as db:
      db.delete(db.get(SourceModel, source.id))
      db.commit()
  enriched = async_runner.run(_write(ArchiveBatch.model_validate(native), tracked))
  assert enriched.block_ids["posts"][post["id"]] == root.id
  assert json.loads(_record(post["id"]).content)["text"] == post["text"]
