"""Bounded backfill traverses provider cursors without changing the ordinary marker."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.job import JobModel
from extensions.twitter import bookmark
from extensions.twitter.api import TwitterAPIResult
from extensions.twitter.model import TwitterBatch, TwitterPost


def page(ids, next_page=None):
  return TwitterAPIResult(
    primary_post_ids=tuple(str(value) for value in ids),
    next_page=next_page,
    batch=TwitterBatch(
      posts=[
        TwitterPost(id=str(value), authorId="1", createdAt="2026-11-04T00:00:00Z")
        for value in ids
      ]
    ),
  )


@pytest.fixture
def capture(monkeypatch):
  saved = []
  marker = {"latest_tweet_id": "42"}
  source = SimpleNamespace(state=marker.copy())
  api = SimpleNamespace(get_bookmarks=AsyncMock())
  monkeypatch.setattr(bookmark.TwitterAPI, "new", AsyncMock(return_value=api))
  repository = SimpleNamespace(get=AsyncMock(return_value=source), save=AsyncMock())

  @asynccontextmanager
  async def uow():
    yield SimpleNamespace(sources=repository, graph=object())

  async def persist(batch, graph, *, text_policy):
    assert text_policy == "supplement"
    saved.extend(post.id for post in batch.posts)

  monkeypatch.setattr(bookmark, "source_uow", uow)
  monkeypatch.setattr(bookmark, "persist_twitter", persist)
  return api, saved, source, repository


def test_backfill_range_crosses_pages_and_stops_at_limit(capture):
  api, saved, source, repository = capture
  api.get_bookmarks.side_effect = [
    page(range(1, 6), "a"),
    page(range(6, 11), "b"),
    page(range(11, 13), "c"),
  ]
  job = JobModel(type="source.backfill", timeout_seconds=60)
  asyncio.run(
    bookmark.Source(3).backfill(job, bookmark.BackfillConfig(skip=7, limit=5, page_size=5))
  )
  assert saved == ["8", "9", "10", "11", "12"]
  assert api.get_bookmarks.call_args_list[-1].kwargs == {"page": "b", "max_results": 2}
  assert api.get_bookmarks.call_count == 3
  assert source.state == {"latest_tweet_id": "42"}
  repository.save.assert_not_called()
  assert job.state == {
    "scanned": 12,
    "saved": 5,
    "pages": 3,
    "exhausted": False,
    "next_page": "c",
  }


def test_backfill_reports_exhaustion_before_requested_range(capture):
  api, saved, _, _ = capture
  api.get_bookmarks.return_value = page([1, 2])
  job = JobModel(type="source.backfill", timeout_seconds=60)
  asyncio.run(bookmark.Source(3).backfill(job, bookmark.BackfillConfig(skip=200)))
  assert saved == []
  assert job.state["scanned"] == 2
  assert job.state["saved"] == 0
  assert job.state["exhausted"] is True
  api.get_bookmarks.assert_awaited_once()


def test_backfill_treats_empty_cursor_as_exhausted(capture):
  api, saved, _, _ = capture
  api.get_bookmarks.return_value = page([], "")
  job = JobModel(type="source.backfill", timeout_seconds=60)
  asyncio.run(bookmark.Source(3).backfill(job, bookmark.BackfillConfig(limit=5)))
  assert saved == []
  assert job.state == {
    "scanned": 0,
    "saved": 0,
    "pages": 1,
    "exhausted": True,
    "next_page": None,
  }
  api.get_bookmarks.assert_awaited_once()


def test_backfill_does_not_report_uncommitted_page_as_saved(capture, monkeypatch):
  api, saved, source, repository = capture
  api.get_bookmarks.side_effect = [page([1, 2], "a"), page([3, 4], "b")]
  persist = bookmark.persist_twitter

  async def fail_second_page(batch, graph, *, text_policy):
    if batch.posts[0].id == "3":
      raise RuntimeError("storage unavailable")
    await persist(batch, graph, text_policy=text_policy)

  monkeypatch.setattr(bookmark, "persist_twitter", fail_second_page)
  job = JobModel(type="source.backfill", timeout_seconds=60)
  with pytest.raises(RuntimeError, match="storage unavailable"):
    asyncio.run(bookmark.Source(3).backfill(job, bookmark.BackfillConfig(limit=5)))
  assert saved == ["1", "2"]
  assert job.state["saved"] == 2
  assert job.state["next_page"] == "a"
  assert source.state == {"latest_tweet_id": "42"}
  repository.save.assert_not_called()


@pytest.mark.parametrize(
  "second,error",
  [
    (page([3], "a"), "repeated"),
    (page([2], "b"), "ordering changed"),
    (page([], "b"), "empty bookmark page"),
  ],
)
def test_backfill_stops_on_ambiguous_provider_continuation(capture, second, error):
  api, saved, _, _ = capture
  api.get_bookmarks.side_effect = [page([1, 2], "a"), second]
  job = JobModel(type="source.backfill", timeout_seconds=60)
  with pytest.raises(ValueError, match=error):
    asyncio.run(bookmark.Source(3).backfill(job, bookmark.BackfillConfig(limit=5)))
  assert saved == ["1", "2"]
  assert job.state["saved"] == 2
