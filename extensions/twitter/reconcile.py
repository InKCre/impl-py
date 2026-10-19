"""One transactional native Twitter archive shared by HTTP and Bookmark sources."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import json
import typing

import pydantic

from app.persistence.info_base.uow import GraphUnitOfWork, graph_uow
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.info_base.relation import RelationCreateForm

from .archive_merge import merge_archive_record
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


POST_RESOLVER = "extensions.twitter.tweet.v2"
USER_RESOLVER = "extensions.twitter.user.v1"
MEDIA_RESOLVER = "extensions.twitter.media.v1"
LEGACY_POST_RESOLVER = "extensions.twitter.tweet.v1"
REFERENCE_SCHEMA = "extensions.twitter.reference.v1"
TWITTER_WRITE_LOCK = "extensions.twitter.reconcile"
KINDS = ("users", "posts", "media")
RESOLVERS = {"users": USER_RESOLVER, "posts": POST_RESOLVER, "media": MEDIA_RESOLVER}
MODELS = {"users": ArchiveUser, "posts": ArchivePost, "media": ArchiveMedia}
Record = dict[str, typing.Any]
Identity = tuple[str, str]


@dataclass(frozen=True)
class ArchiveWriteResult:
  response: SubmitResponse
  block_ids: dict[str, dict[str, int]]


def _json(value: object) -> str:
  return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _block_id(block: BlockModel) -> int:
  if block.id is None:
    raise RuntimeError("Twitter Block has no persisted identity")
  return block.id


def _references(post: Record) -> tuple[tuple[str, Identity, int | None], ...]:
  refs: list[tuple[str, Identity, int | None]] = []
  if "authorId" in post:
    refs.append(("author", ("users", post["authorId"]), None))
  article = post.get("article", {})
  media_ids = list(post.get("mediaIds", ()))
  if "coverMediaId" in article:
    media_ids.append(article["coverMediaId"])
  media_ids.extend(article.get("mediaIds", ()))
  for position, media_id in enumerate(dict.fromkeys(media_ids)):
    refs.append(("media", ("media", media_id), position))
  for field, kind in (("replyTo", "reply"), ("quote", "quote")):
    if field in post:
      refs.append((kind, ("posts", post[field]["postId"]), None))
  if "repostOf" in post:
    refs.append(("repost", ("posts", post["repostOf"]), None))
  return tuple(refs)


def _reference_content(kind: str, position: int | None) -> str:
  content: Record = {"schema": REFERENCE_SCHEMA, "type": kind}
  if position is not None:
    content["position"] = position
  return _json(content)


def _is_reference(content: str) -> bool:
  try:
    value = json.loads(content)
  except ValueError:
    return False
  if not isinstance(value, dict) or value.get("schema") != REFERENCE_SCHEMA:
    return False
  if value.get("type") == "media":
    position = value.get("position")
    return (
      set(value) == {"schema", "type", "position"}
      and type(position) is int
      and position >= 0
    )
  return set(value) == {"schema", "type"} and value.get("type") in {
    "author",
    "reply",
    "quote",
    "repost",
  }


async def _load_roots(
  identities: Collection[Identity],
  uow: GraphUnitOfWork,
  *,
  include_legacy: bool = False,
) -> dict[Identity, BlockModel]:
  result: dict[Identity, BlockModel] = {}
  for kind in KINDS:
    ids = {native_id for candidate_kind, native_id in identities if candidate_kind == kind}
    resolvers = (RESOLVERS[kind],)
    if include_legacy and kind == "posts":
      resolvers += (LEGACY_POST_RESOLVER,)
    candidates = await uow.blocks.find_json_field_many(resolvers, "id", ids)
    for block in sorted(
      candidates,
      key=lambda block: (block.resolver == LEGACY_POST_RESOLVER, _block_id(block)),
    ):
      native_id = str(json.loads(block.content)["id"])
      result.setdefault((kind, native_id), block)
  return result


async def persist_archive(
  batch: ArchiveBatch,
  uow: GraphUnitOfWork,
  *,
  preserve_existing_text: bool = False,
) -> ArchiveWriteResult:
  """Merge a complete batch without committing the caller's graph transaction.

  Bookmark can supplement a native record, but its less detailed body must not
  replace an already captured text; x-monkey keeps the native merge semantics.
  """
  await uow.blocks.acquire_transaction_lock(TWITTER_WRITE_LOCK)
  incoming: dict[Identity, Record] = {}
  for kind in KINDS:
    for record in getattr(batch, kind):
      key = (kind, record.id)
      incoming[key] = merge_archive_record(
        incoming.get(key, {}), record.model_dump(mode="json", exclude_unset=True)
      )
  identities = set(incoming)
  for (kind, _), record in incoming.items():
    if kind == "posts":
      identities.update(target for _, target, _ in _references(record))
  roots = await _load_roots(identities, uow, include_legacy=True)
  changed: set[Identity] = set()
  records: dict[Identity, Record] = {}
  new: list[tuple[Identity, BlockForm]] = []
  updated: list[BlockModel] = []
  for key in sorted(identities):
    root = roots.get(key)
    existing: Record = (
      json.loads(root.content)
      if root is not None and root.resolver != LEGACY_POST_RESOLVER
      else {"id": key[1]}
    )
    value: Record = incoming.get(key, {"id": key[1]})
    if preserve_existing_text and "text" in existing and "text" in value:
      value = {field: item for field, item in value.items() if field != "text"}
    merged = merge_archive_record(existing, value)
    if key in incoming:
      MODELS[key[0]].model_validate(merged)
    records[key] = merged
    if root is None:
      new.append((key, BlockForm(resolver=RESOLVERS[key[0]], content=_json(merged))))
      changed.add(key)
    elif key in incoming and (
      _json(merged) != _json(existing) or root.resolver != RESOLVERS[key[0]]
    ):
      root.content = _json(merged)
      root.resolver = RESOLVERS[key[0]]
      root.storage = None
      updated.append(root)
      changed.add(key)
  for (key, _), root in zip(
    new, await uow.blocks.create_many(form for _, form in new), strict=True
  ):
    roots[key] = root
  await uow.blocks.save_many(updated)

  # Merged, retained references may be absent from this sparse input.
  retained = {
    target
    for (kind, _), record in records.items()
    if kind == "posts"
    for _, target, _ in _references(record)
  } - roots.keys()
  if retained:
    roots.update(await _load_roots(retained, uow, include_legacy=True))
    absent = sorted(retained - roots.keys())
    placeholders = await uow.blocks.create_many(
      BlockForm(resolver=RESOLVERS[key[0]], content=_json({"id": key[1]})) for key in absent
    )
    roots.update(zip(absent, placeholders, strict=True))

  desired: set[tuple[int, int, str]] = set()
  post_ids: set[int] = set()
  for key in incoming:
    root_id = _block_id(roots[key])
    record = records[key]
    if key[0] == "posts":
      post_ids.add(root_id)
      desired.update(
        (root_id, _block_id(roots[target]), _reference_content(kind, position))
        for kind, target, position in _references(record)
      )
  stale: list[int] = []
  for relation in await uow.relations.get_outgoing_many(post_ids):
    managed = _is_reference(relation.content)
    if not managed:
      continue
    identity = (relation.from_, relation.to_, relation.content)
    if identity in desired:
      desired.remove(identity)
    elif relation.id is not None:
      stale.append(relation.id)
  await uow.relations.delete_many(stale)
  await uow.relations.create_many(
    RelationCreateForm(from_=source, to_=target, content=content)
    for source, target, content in sorted(desired)
  )
  results = {
    kind: [
      SubmitResult(
        id=record.id,
        status="accepted" if (kind, record.id) in changed else "unchanged",
      )
      for record in getattr(batch, kind)
    ]
    for kind in KINDS
  }
  total = sum(len(items) for items in results.values())
  accepted = sum(item.status == "accepted" for items in results.values() for item in items)
  return ArchiveWriteResult(
    response=SubmitResponse(
      summary=SubmitSummary(
        total=total, accepted=accepted, unchanged=total - accepted, partial=0, failed=0
      ),
      **results,
    ),
    block_ids={
      kind: {
        native_id: _block_id(root)
        for (candidate_kind, native_id), root in roots.items()
        if candidate_kind == kind
      }
      for kind in KINDS
    },
  )


async def submit_archive(batch: ArchiveBatch) -> SubmitResponse:
  async with graph_uow() as uow:
    result = await persist_archive(batch, uow)
  return result.response


async def query_archive(query: ArchiveQuery) -> QueryResponse:
  identities = {(kind, native_id) for kind in KINDS for native_id in getattr(query, kind)}
  async with graph_uow() as uow:
    roots = await _load_roots(identities, uow)
    results: dict[str, list[Record]] = {kind: [] for kind in KINDS}
    for kind in KINDS:
      for native_id in getattr(query, kind):
        root = roots.get((kind, native_id))
        if root is None or json.loads(root.content) == {"id": native_id}:
          result: Record = {"id": native_id, "status": "missing"}
        else:
          try:
            data = MODELS[kind].model_validate_json(root.content)
          except pydantic.ValidationError:
            result = {
              "id": native_id,
              "status": "failed",
              "error": "Stored archive record is invalid",
            }
          else:
            result = {
              "id": native_id,
              "status": "found",
              "data": data.model_dump(mode="json", exclude_unset=True),
            }
        results[kind].append(result)
  return QueryResponse.model_validate(results)
