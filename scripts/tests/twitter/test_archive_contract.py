"""Observable native archive round trips and x-monkey capture merge semantics."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pydantic
import pytest

from extensions.twitter.archive_merge import merge_archive_record
from extensions.twitter.archive_schema import (
  ArchiveBatch,
  ArchivePost,
  ArchiveQuery,
  QueryResponse,
  SessionResponse,
  SubmitResponse,
)


@pytest.fixture
def full_archive() -> dict[str, Any]:
  fixture_path = Path(__file__).parent / "fixtures" / "native_archive.json"
  return json.loads(fixture_path.read_text(encoding="utf-8"))


def test_native_batch_preserves_complete_source_and_field_presence(full_archive):
  original = copy.deepcopy(full_archive)
  batch = ArchiveBatch.model_validate(full_archive)
  assert batch.model_dump(mode="json", exclude_unset=True) == original
  assert json.loads(batch.model_dump_json(exclude_unset=True)) == original
  assert full_archive == original
  assert batch.posts[0].communityNote is not None
  assert batch.posts[0].communityNote.text.model_dump(exclude_unset=True) == {
    "text": "来源已核实"
  }


def test_missing_and_explicit_empty_batches_remain_distinct():
  batch = ArchiveBatch.model_validate({})
  query = ArchiveQuery.model_validate({})
  assert batch.users == batch.posts == batch.media == []
  assert query.users == query.posts == query.media == []
  assert batch.model_dump(mode="json", exclude_unset=True) == {}
  assert query.model_dump(mode="json", exclude_unset=True) == {}
  assert ArchiveBatch(posts=[]).model_dump(mode="json", exclude_unset=True) == {"posts": []}


@pytest.mark.parametrize(
  "invalid_id", ["0", "01", "-1", "1.0", " 1", "1\n", "1１", 1, True]
)
def test_ids_are_positive_decimal_strings(invalid_id):
  with pytest.raises(pydantic.ValidationError):
    ArchiveQuery.model_validate({"posts": [invalid_id]})


@pytest.mark.parametrize(
  ("path", "invalid"),
  [
    (("users", 0, "counts", "likes"), True),
    (("users", 0, "counts", "likes"), -1),
    (("users", 0, "counts", "likes"), 1.0),
    (("users", 0, "counts", "likes"), "1"),
    (("posts", 0, "createdAt"), "2026-02-29T00:00:00Z"),
    (("posts", 0, "createdAt"), "2026-10-19T00:00:00.123Z"),
    (("posts", 0, "createdAt"), "2026-10-19T00:00:00+00:00"),
    (("posts", 0, "countsObservedAt", "likes"), "2026-10-19T24:00:00Z"),
    (("posts", 0, "countsObservedAt", "likes"), "2026-10-19T01:00:00+08:00"),
    (("posts", 0, "countsObservedAt", "likes"), "2026-10-19T01:00:00"),
    (("posts", 0, "countsObservedAt", "likes"), "2026-10-19T01:00Z"),
    (("users", 0, "countsObservedAt", "posts"), "2026-10-19T01:02Z"),
    (("posts", 0, "text", "visible"), [9, 2]),
    (("posts", 0, "text", "visible"), [False, 2]),
    (("posts", 0, "text", "entities"), []),
    (("posts", 0, "mediaIds"), []),
    (("posts", 0, "editIds"), ["31"]),
    (("posts", 0, "card", "poll", "choices"), [{"label": "only one"}]),
    (("posts", 0, "card", "extra"), {"unexpected": 1}),
    (("posts", 0, "place", "bbox"), [0, True, 2, 3]),
    (("posts", 0, "place", "bbox"), [0, float("inf"), 2, 3]),
    (("posts", 0, "quote", "tombstone"), ""),
    (("media", 0, "video", "variants"), []),
    (("media", 0, "crops"), [[0, 0, -1, 10]]),
    (("posts", 0, "text"), None),
    (("users", 0, "verified", "blue"), None),
    (("posts", 0, "article", "blocks", 0, "unknown"), True),
    (("media", 0, "unknown"), "discarding this would lose information"),
  ],
)
def test_invalid_native_records_fail_instead_of_coercing_or_discarding(
  full_archive, path, invalid
):
  current = full_archive
  for part in path[:-1]:
    current = current[part]
  current[path[-1]] = invalid
  with pytest.raises(pydantic.ValidationError):
    ArchiveBatch.model_validate(full_archive)


@pytest.mark.parametrize("kind", ["users", "posts", "media"])
def test_query_and_submission_have_per_kind_limits(full_archive, kind):
  assert len(getattr(ArchiveQuery.model_validate({kind: ["1"] * 1000}), kind)) == 1000
  with pytest.raises(pydantic.ValidationError):
    ArchiveQuery.model_validate({kind: ["1"] * 1001})
  record = full_archive[kind][0]
  assert len(getattr(ArchiveBatch.model_validate({kind: [record] * 1000}), kind)) == 1000
  with pytest.raises(pydantic.ValidationError):
    ArchiveBatch.model_validate({kind: [record] * 1001})


def test_query_submit_and_session_wire_roundtrip(full_archive):
  query = {
    "users": [{"id": "17", "status": "found", "data": full_archive["users"][0]}],
    "posts": [{"id": "31", "status": "missing"}],
    "media": [{"id": "41", "status": "failed", "error": "invalid stored archive"}],
  }
  assert (
    QueryResponse.model_validate(query).model_dump(mode="json", exclude_unset=True) == query
  )
  submission = {
    "summary": {"total": 4, "accepted": 1, "unchanged": 1, "partial": 1, "failed": 1},
    "users": [{"id": "17", "status": "accepted"}],
    "posts": [{"id": "31", "status": "unchanged"}, {"id": "32", "status": "partial"}],
    "media": [{"id": "41", "status": "failed", "reason": "invalid media"}],
  }
  assert (
    SubmitResponse.model_validate(submission).model_dump(mode="json", exclude_unset=True)
    == submission
  )
  session = {
    "authenticated": False,
    "registered": False,
    "username": None,
    "expires_at": None,
    "account_url": None,
  }
  assert SessionResponse.model_validate(session).model_dump(mode="json") == session
  with pytest.raises(pydantic.ValidationError):
    ArchivePost.model_validate({"id": "31", "authorId": "17", "createdAt": None})


def test_merge_is_recursive_replaces_arrays_and_does_not_mutate_inputs():
  existing: dict[str, Any] = {
    "id": "31",
    "text": {"text": "正文", "visible": [0, 2], "entities": [{"type": "hashtag"}]},
    "article": {"title": "长文", "blocks": [{"text": "第一版"}]},
  }
  incoming: dict[str, Any] = {
    "text": {"text": "修订正文"},
    "article": {"blocks": [{"text": "完整新版"}]},
  }
  old_existing, old_incoming = copy.deepcopy(existing), copy.deepcopy(incoming)
  merged: dict[str, Any] = merge_archive_record(existing, incoming)
  assert merged == {
    "id": "31",
    "text": {"text": "修订正文", "visible": [0, 2], "entities": [{"type": "hashtag"}]},
    "article": {"title": "长文", "blocks": [{"text": "完整新版"}]},
  }
  merged["article"]["blocks"][0]["text"] = "调用方修改"
  merged["text"]["entities"][0]["type"] = "调用方修改"
  assert existing == old_existing
  assert incoming == old_incoming


def test_counter_observations_merge_independently_and_compare_instants():
  existing: dict[str, Any] = {
    "counts": {"likes": 10, "views": 100, "quotes": 3},
    "countsObservedAt": {
      "likes": "2026-10-19T01:00:00.900Z",
      "views": "2026-10-19T01:00:00Z",
      "quotes": "2026-10-19T01:00:00Z",
    },
  }
  merged = merge_archive_record(
    existing,
    {
      "lang": "zh",
      "counts": {"likes": 9, "views": 110, "quotes": 2, "bookmarks": 0},
      "countsObservedAt": {
        "likes": "2026-10-19T01:00:00Z",
        "views": "2026-10-19T01:00:00.100Z",
        "quotes": "2026-10-19T01:00:00Z",
      },
    },
  )
  assert merged == {
    "lang": "zh",
    "counts": {"likes": 10, "views": 110, "quotes": 2, "bookmarks": 0},
    "countsObservedAt": {
      "likes": "2026-10-19T01:00:00.900Z",
      "views": "2026-10-19T01:00:00.100Z",
      "quotes": "2026-10-19T01:00:00Z",
    },
  }


def test_untimed_changes_remove_only_the_changed_counters_previous_time():
  existing: dict[str, Any] = {
    "counts": {"likes": 10, "views": 100},
    "countsObservedAt": {"likes": "2026-10-19T01:00:00Z", "views": "2026-10-19T01:00:00Z"},
  }
  assert merge_archive_record(existing, {"counts": {"likes": 11, "views": 100}}) == {
    "counts": {"likes": 11, "views": 100},
    "countsObservedAt": {"views": "2026-10-19T01:00:00Z"},
  }
  assert merge_archive_record(existing, {"counts": {"likes": 11, "views": 101}}) == {
    "counts": {"likes": 11, "views": 101}
  }


def test_equal_counts_keep_new_observation_metadata_and_replay_is_unchanged():
  existing: dict[str, Any] = {
    "counts": {"likes": 10},
    "countsObservedAt": {"likes": "2026-10-19T01:00:00Z"},
  }
  incoming: dict[str, Any] = {
    "counts": {"likes": 10},
    "countsObservedAt": {"likes": "2026-10-19T01:00:00.001Z"},
  }
  merged = merge_archive_record(existing, incoming)
  assert merged == incoming
  assert merged != existing
  assert merge_archive_record(merged, incoming) == merged
  assert (
    merge_archive_record(existing, {"countsObservedAt": incoming["countsObservedAt"]})
    == existing
  )


def test_capture_time_comparison_matches_javascript_millisecond_precision():
  existing: dict[str, Any] = {
    "counts": {"likes": 10},
    "countsObservedAt": {"likes": "2026-10-19T01:00:00.1009Z"},
  }
  incoming: dict[str, Any] = {
    "counts": {"likes": 11},
    "countsObservedAt": {"likes": "2026-10-19T01:00:00.1001Z"},
  }
  assert merge_archive_record(existing, incoming) == incoming
