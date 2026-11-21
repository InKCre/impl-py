"""Optional feed item enrichment and enclosure materialization commands."""

from __future__ import annotations

import asyncio
import dataclasses
import mimetypes
from contextlib import nullcontext
from functools import partial
from urllib.parse import urlparse

import trafilatura

from app.business.info_base.resolver import ResolverManager
from app.business.info_base.resolver.inspection import detect_media_type
from app.business.info_base.storage import StorageManager, WritableStorage
from app.persistence.source.uow import SourceUnitOfWork, source_uow
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.info_base.relation import RelationModel

from .http import HTTPFetchOptions, fetch_http_bytes
from .reconcile import (
  CONTENT_RELATION,
  ENCLOSURE_RELATION,
  ENCLOSURE_RESOLVER_ID,
  FEED_ITEM_RESOLVER_ID,
  FEED_RELATION,
  FEED_RESOLVER_ID,
  FULL_TEXT_RELATION,
  FeedGraphIntegrityError,
)
from .schema import CanonicalEnclosure, CanonicalFeed, CanonicalFeedItem, FeedSourceConfig


@dataclasses.dataclass(frozen=True)
class MaterializationResult:
  enclosure_block_id: int
  content_block_id: int | None
  status: str
  resolver_id: str | None = None
  error: str | None = None


@dataclasses.dataclass(frozen=True)
class FullTextResult:
  item_block_id: int
  content_block_id: int | None
  status: str


def _required_id(block: BlockModel) -> int:
  if block.id is None:
    raise RuntimeError("Persisted block is missing its database ID")
  return block.id


async def _one_outgoing_relation(
  block_id: int,
  content: str,
  uow: SourceUnitOfWork,
) -> RelationModel | None:
  relations = tuple(
    relation
    for relation in await uow.graph.relations.get(
      block_id,
      include_in=False,
      include_out=True,
      content=content,
    )
    if relation.from_ == block_id
  )
  if len(relations) > 1:
    raise FeedGraphIntegrityError(
      f"block {block_id} has more than one {content!r} relation"
    )
  return relations[0] if relations else None


async def _source_config_for_item(
  item_block_id: int,
  uow: SourceUnitOfWork,
) -> FeedSourceConfig:
  feed_relation = await _one_outgoing_relation(item_block_id, FEED_RELATION, uow)
  if feed_relation is None:
    raise FeedGraphIntegrityError(f"feed item {item_block_id} has no feed relation")
  feed_block = await uow.graph.blocks.get(feed_relation.to_)
  if feed_block is None or feed_block.resolver != FEED_RESOLVER_ID:
    raise FeedGraphIntegrityError(
      f"feed item {item_block_id} references an invalid feed root"
    )
  feed = CanonicalFeed.model_validate_json(feed_block.content)
  source = await uow.sources.get(feed.source_instance_id)
  if source is None:
    raise FeedGraphIntegrityError(
      f"feed root {feed_block.id} references a missing source instance"
    )
  return FeedSourceConfig.model_validate(source.config)


async def _source_config_for_enclosure(
  enclosure_block_id: int,
  uow: SourceUnitOfWork,
) -> FeedSourceConfig:
  owner_relations = tuple(
    relation
    for relation in await uow.graph.relations.get(
      enclosure_block_id,
      include_in=True,
      include_out=False,
      content=ENCLOSURE_RELATION,
    )
    if relation.to_ == enclosure_block_id
  )
  if len(owner_relations) != 1:
    raise FeedGraphIntegrityError(
      f"enclosure {enclosure_block_id} must have exactly one feed item owner"
    )
  return await _source_config_for_item(owner_relations[0].from_, uow)


def _extract_main_text(body: bytes, url: str) -> str | None:
  extracted = trafilatura.extract(
    body,
    url=url,
    include_comments=False,
    include_tables=True,
    output_format="txt",
  )
  if extracted is None:
    return None
  normalized = extracted.strip()
  return normalized or None


class FullTextEnrichmentService:
  """Materialize a derived main-text child without changing primary authority."""

  @classmethod
  async def materialize(
    cls,
    item_block_id: int,
    *,
    refresh: bool = False,
    require_enabled: bool = False,
  ) -> FullTextResult:
    async with source_uow() as uow:
      item_block = await uow.graph.blocks.get(item_block_id)
      if item_block is None or item_block.resolver != FEED_ITEM_RESOLVER_ID:
        raise LookupError(f"feed item block {item_block_id} not found")
      item = CanonicalFeedItem.model_validate_json(item_block.content)
      existing_relation = await _one_outgoing_relation(
        item_block_id,
        FULL_TEXT_RELATION,
        uow,
      )
      if existing_relation is not None and not refresh:
        return FullTextResult(item_block_id, existing_relation.to_, "existing")
      source_config = await _source_config_for_item(item_block_id, uow)

    if require_enabled and not source_config.fetch_full_text:
      return FullTextResult(item_block_id, None, "unavailable")
    if item.alternate_url is None:
      return FullTextResult(item_block_id, None, "unavailable")
    response = await fetch_http_bytes(
      item.alternate_url,
      options=HTTPFetchOptions(
        timeout_seconds=source_config.request_timeout_seconds,
        max_response_bytes=source_config.max_article_bytes,
        user_agent=source_config.user_agent,
      ),
    )
    text = await asyncio.to_thread(_extract_main_text, response.body, item.alternate_url)
    if text is None:
      return FullTextResult(item_block_id, None, "unavailable")

    async with source_uow() as uow:
      locked = await uow.graph.blocks.get(item_block_id, lock=True)
      if locked is None or locked.resolver != FEED_ITEM_RESOLVER_ID:
        raise FeedGraphIntegrityError(
          f"block {item_block_id} no longer has its expected resolver"
        )
      relation = await _one_outgoing_relation(
        item_block_id,
        FULL_TEXT_RELATION,
        uow,
      )
      if relation is not None:
        content_block = await uow.graph.blocks.get(relation.to_)
        if content_block is None or content_block.resolver != "core.text.v1":
          raise FeedGraphIntegrityError(
            f"full_text relation from item {item_block_id} targets invalid content"
          )
        if refresh and content_block.content != text:
          content_block.content = text
          await uow.graph.blocks.save(content_block)
          status = "updated"
        else:
          status = "existing"
      else:
        content_block = await uow.graph.blocks.create(
          BlockForm(resolver="core.text.v1", content=text),
        )
        await uow.graph.relations.create(
          item_block_id,
          _required_id(content_block),
          FULL_TEXT_RELATION,
        )
        status = "created"
      return FullTextResult(item_block_id, _required_id(content_block), status)


def _filename_media_type(url: str) -> str | None:
  return mimetypes.guess_type(urlparse(url).path)[0]


def _resolver_for_media_type(media_type: str | None) -> str | None:
  return ResolverManager.match_media_type(media_type)


def select_enclosure_resolver(
  enclosure: CanonicalEnclosure,
  *,
  observed_content_type: str | None,
  body: bytes,
) -> str:
  """Apply the protocol-specific evidence ladder, then explicit file fallback."""
  if enclosure.family == "rss":
    candidates = (
      enclosure.declared_media_type,
      observed_content_type,
      _filename_media_type(enclosure.url),
      detect_media_type(body),
    )
  else:
    candidates = (
      observed_content_type,
      enclosure.declared_media_type,
      _filename_media_type(enclosure.url),
      detect_media_type(body),
    )
  return next(
    (
      resolver_id
      for candidate in candidates
      if (resolver_id := _resolver_for_media_type(candidate)) is not None
    ),
    "core.file.v1",
  )


class EnclosureMaterializationService:
  """Download one enclosure and create at most one semantic storage child."""

  @staticmethod
  async def _existing_content(
    enclosure_block_id: int, uow: SourceUnitOfWork
  ) -> MaterializationResult | None:
    existing = await _one_outgoing_relation(enclosure_block_id, CONTENT_RELATION, uow)
    if existing is None:
      return None
    child = await uow.graph.blocks.get(existing.to_)
    if child is None:
      raise FeedGraphIntegrityError(
        f"enclosure {enclosure_block_id} content child is missing"
      )
    return MaterializationResult(
      enclosure_block_id, existing.to_, "existing", child.resolver
    )

  @classmethod
  async def materialize(
    cls,
    enclosure_block_id: int,
    *,
    target_storage_id: int,
  ) -> MaterializationResult:
    async with source_uow() as uow:
      enclosure_block = await uow.graph.blocks.get(enclosure_block_id)
      if enclosure_block is None or enclosure_block.resolver != ENCLOSURE_RESOLVER_ID:
        raise LookupError(f"enclosure block {enclosure_block_id} not found")
      enclosure = CanonicalEnclosure.model_validate_json(enclosure_block.content)
      existing = await cls._existing_content(enclosure_block_id, uow)
      if existing is not None:
        return existing
      source_config = await _source_config_for_enclosure(enclosure_block_id, uow)
      record = await uow.graph.storage.get(target_storage_id)
      if record is None:
        raise LookupError(f"storage {target_storage_id} does not exist")
      storage = StorageManager.from_record(record)
      if not isinstance(storage, WritableStorage):
        raise TypeError(f"storage {target_storage_id} is not writable")

    response = await fetch_http_bytes(
      enclosure.url,
      options=HTTPFetchOptions(
        timeout_seconds=source_config.request_timeout_seconds,
        max_response_bytes=source_config.max_enclosure_bytes,
        user_agent=source_config.user_agent,
      ),
    )
    resolver_id = select_enclosure_resolver(
      enclosure,
      observed_content_type=response.content_type,
      body=response.body,
    )

    # Older Core releases write bytes in the graph's transaction.
    write = (
      storage.prepare_write(response.body)
      if hasattr(storage, "prepare_write")
      else nullcontext(partial(storage.create_content, response.body))
    )
    async with write as write_pointer, source_uow() as uow:
      locked = await uow.graph.blocks.get(enclosure_block_id, lock=True)
      if locked is None or locked.resolver != ENCLOSURE_RESOLVER_ID:
        raise FeedGraphIntegrityError(
          f"block {enclosure_block_id} no longer has its expected resolver"
        )
      existing = await cls._existing_content(enclosure_block_id, uow)
      if existing is not None:
        return existing
      pointer = await write_pointer(uow.graph.storage)
      content_block = await uow.graph.blocks.create(
        BlockForm(
          resolver=resolver_id,
          storage=target_storage_id,
          content=pointer,
        ),
      )
      await uow.graph.relations.create(
        enclosure_block_id,
        _required_id(content_block),
        CONTENT_RELATION,
      )
      return MaterializationResult(
        enclosure_block_id,
        _required_id(content_block),
        "created",
        resolver_id,
      )


__all__ = [
  "EnclosureMaterializationService",
  "FullTextEnrichmentService",
  "FullTextResult",
  "MaterializationResult",
  "select_enclosure_resolver",
]
