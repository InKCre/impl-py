"""Translate the fixed x-monkey protocol to Twitter-owned capture operations."""

from __future__ import annotations

import pydantic
from pydantic import JsonValue

from .archive_schema import (
  ArchiveBatch,
  ArchiveMedia,
  ArchivePost,
  ArchiveQuery,
  ArchiveUser,
  QueryResponse,
  SubmitResponse,
  SubmitResult,
  SubmitSummary,
)
from .model import TwitterBatch, TwitterRecord
from .reconcile import (
  KINDS,
  TwitterReadResult,
  TwitterWriteResult,
  read_twitter,
  write_twitter,
)


WIRE_MODELS = {"users": ArchiveUser, "posts": ArchivePost, "media": ArchiveMedia}


def decode_submission(batch: ArchiveBatch) -> TwitterBatch:
  """Map accepted provider facts without manufacturing absent values."""
  return TwitterBatch.model_validate(batch.model_dump(mode="json", exclude_unset=True))


def export_record(kind: str, record: TwitterRecord) -> JsonValue:
  """Project native facts into the supported wire version, then validate that DTO."""
  model = WIRE_MODELS[kind]
  projected = model.model_validate(
    record.model_dump(mode="json", exclude_unset=True), extra="ignore"
  )
  return projected.model_dump(mode="json", exclude_unset=True)


def encode_submission(batch: ArchiveBatch, result: TwitterWriteResult) -> SubmitResponse:
  results = {
    kind: [
      SubmitResult(
        id=record.id,
        status="accepted" if (kind, record.id) in result.changed else "unchanged",
      )
      for record in getattr(batch, kind)
    ]
    for kind in KINDS
  }
  total = sum(len(items) for items in results.values())
  accepted = sum(item.status == "accepted" for items in results.values() for item in items)
  return SubmitResponse(
    summary=SubmitSummary(
      total=total, accepted=accepted, unchanged=total - accepted, partial=0, failed=0
    ),
    **results,
  )


def encode_query(query: ArchiveQuery, result: TwitterReadResult) -> QueryResponse:
  results: dict[str, list[dict[str, JsonValue]]] = {kind: [] for kind in KINDS}
  for kind in KINDS:
    for native_id in getattr(query, kind):
      key = (kind, native_id)
      item: dict[str, JsonValue] = {"id": native_id, "status": "missing"}
      record = result.records.get(key)
      if key in result.invalid:
        item.update(status="failed", error="Stored archive record is invalid")
      elif record is not None:
        try:
          data = export_record(kind, record)
        except pydantic.ValidationError:
          item.update(status="failed", error="Stored record cannot use this archive format")
        else:
          item.update(status="found", data=data)
      results[kind].append(item)
  return QueryResponse.model_validate(results)


async def submit_archive(batch: ArchiveBatch) -> SubmitResponse:
  return encode_submission(batch, await write_twitter(decode_submission(batch)))


async def query_archive(query: ArchiveQuery) -> QueryResponse:
  identities = {(kind, native_id) for kind in KINDS for native_id in getattr(query, kind)}
  return encode_query(query, await read_twitter(identities))
