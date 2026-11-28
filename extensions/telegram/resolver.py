"""Legacy Telegram messages and metadata-first attachment materialization."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import telegram

from app.business.info_base.services import BlockService
from app.business.info_base.resolver import Resolver, ResolverManager, TextProjectionContext
from app.business.info_base.resolver.inspection import detect_media_type
from app.business.info_base.resolver.label import format_label
from app.business.source.config import resolve_writable_storage_async
from app.persistence.source.uow import source_uow
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.source import SourceModel

from .schema import SolvedTelegramAttachment, TelegramAttachment, TelegramMessage


ATTACHMENT_RESOLVER = "extensions.telegram.attachment.v1"
CONTENT_RELATION = "content"
SOURCE_RELATION = "collects"


class TelegramMaterializationUnavailable(RuntimeError):
  """The attachment's exact live Telegram access context is unavailable."""


def _id(block: BlockModel) -> int:
  if block.id is None:
    raise RuntimeError("persisted Block has no ID")
  return block.id


class TelegramMessageResolver(
  Resolver[TelegramMessage, str],
  rso_type="extensions.telegram.message.v1",
):
  """Read-only decoder for published 0.1.0 Telegram message Blocks."""

  def __post_init__(self, raw_content):
    if raw_content is not None:
      self.set_solved_content(TelegramMessage.model_validate_json(raw_content))

  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> TelegramMessage:
    del materialize_missing
    return TelegramMessage.model_validate_json(await self.get_raw_content(refresh=refresh))

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str | None:
    del context
    message = await self.get_solved_content(
      refresh=refresh, materialize_missing=materialize_missing
    )
    parts = [message.text or message.caption]
    if message.has_media and message.media_type:
      parts.append(f"[{message.media_type}]")
    return "\n".join(part for part in parts if part) or None

  async def get_label(self, *, refresh: bool = False) -> str:
    message = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    return format_label(
      "telegram message",
      message.text or message.caption or str(message.message_id),
      first_line=True,
    )


class TelegramAttachmentResolver(
  Resolver[SolvedTelegramAttachment, str],
  rso_type=ATTACHMENT_RESOLVER,
):
  """Project useful remote-file metadata without implicit network effects."""

  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> SolvedTelegramAttachment:
    del materialize_missing
    root = TelegramAttachment.model_validate_json(
      await self.get_raw_content(refresh=refresh)
    )
    relations = await self.get_relations(
      include_in=False, include_out=True, refresh=refresh
    )
    content_ids = [
      relation.to_ for relation in relations if relation.content == CONTENT_RELATION
    ]
    if len(content_ids) > 1:
      raise RuntimeError(
        f"Telegram attachment {self.block_id} has multiple content children"
      )
    content = await BlockService.get(content_ids[0]) if content_ids else None
    return SolvedTelegramAttachment(root=root, content=content)

  @classmethod
  def create_block(
    cls, content: TelegramAttachment, storage: int | None = None
  ) -> BlockForm:
    return BlockForm(
      resolver=cls.__rsotype__, content=content.model_dump_json(), storage=storage
    )

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str | None:
    del context
    solved = await self.get_solved_content(
      refresh=refresh, materialize_missing=materialize_missing
    )
    values = [
      solved.root.filename,
      solved.root.title,
      solved.root.performer,
      solved.root.mime_type,
      solved.root.kind,
      solved.root.emoji,
    ]
    return "\n".join(value for value in values if value) or None

  async def get_label(self, *, refresh: bool = False) -> str:
    root = TelegramAttachment.model_validate_json(
      await self.get_raw_content(refresh=refresh)
    )
    return format_label(f"telegram {root.kind}", root.filename or root.title or root.emoji)

  async def _source(self) -> SourceModel:
    async with source_uow() as uow:
      relations = await uow.graph.relations.get(
        self.block_id, include_out=False, content=SOURCE_RELATION
      )
      sources = [
        source
        for relation in relations
        if (source := await uow.sources.get_by_block(relation.from_)) is not None
      ]
      if len(sources) != 1:
        raise TelegramMaterializationUnavailable(
          "Telegram attachment has no unique live owning Source"
        )
      return sources[0]

  async def materialize_content(self) -> BlockModel:
    existing = await self.get_solved_content(materialize_missing=False)
    if existing.content is not None:
      return existing.content
    source = await self._source()
    from .schema import TelegramSourceConfig, TelegramSourceState

    setup = TelegramSourceConfig.model_validate(source.config)
    state = TelegramSourceState.model_validate(source.state or {})
    if state.bot_id is None:
      raise TelegramMaterializationUnavailable("Telegram Source has no pinned bot identity")
    root = TelegramAttachment.model_validate_json(await self.get_raw_content())
    try:
      async with telegram.Bot(setup.bot_token) as bot:
        identity = await bot.get_me()
        if identity.id != state.bot_id:
          raise TelegramMaterializationUnavailable("Telegram Source bot identity changed")
        remote = await bot.get_file(root.file_id)
        body = bytes(await remote.download_as_bytearray())
    except TelegramMaterializationUnavailable:
      raise
    except telegram.error.BadRequest as error:
      raise TelegramMaterializationUnavailable(str(error)) from error

    declared = ResolverManager.match_media_type(root.mime_type)
    detected = ResolverManager.match_media_type(detect_media_type(body))
    guessed = ResolverManager.match_media_type(
      mimetypes.guess_type(Path(root.filename).name)[0] if root.filename else None
    )
    by_kind = {
      "photo": "core.image.v1",
      "sticker": "core.image.v1",
      "animation": "core.video.v1",
      "video": "core.video.v1",
      "video_note": "core.video.v1",
      "audio": "core.audio.v1",
      "voice": "core.audio.v1",
    }.get(root.kind)
    resolver_id = detected or declared or guessed or by_kind or "core.file.v1"

    async with source_uow() as uow:
      metadata = await uow.graph.blocks.get(self.block_id, lock=True)
      if metadata is None or metadata.resolver != ATTACHMENT_RESOLVER:
        raise TelegramMaterializationUnavailable("Telegram attachment no longer exists")
      relations = await uow.graph.relations.get(
        self.block_id, include_in=False, content=CONTENT_RELATION
      )
      if len(relations) > 1:
        raise RuntimeError("Telegram attachment has multiple content children")
      existing_relation = relations[0] if relations else None
      if existing_relation is not None:
        existing = await uow.graph.blocks.get(existing_relation.to_)
        if existing is None:
          raise RuntimeError("Telegram attachment content child is missing")
        return existing
      if source.id is None:
        raise TelegramMaterializationUnavailable("Telegram Source has no identity")
      live_source = await uow.sources.get(source.id)
      if live_source is None:
        raise TelegramMaterializationUnavailable("Telegram Source no longer exists")
      storage = await resolve_writable_storage_async(live_source, uow)
      pointer = await storage.create_content(body, uow.graph.storage)
      child = await uow.graph.blocks.create(
        BlockForm(storage=storage.storage_id, resolver=resolver_id, content=pointer)
      )
      await uow.graph.relations.create(_id(metadata), _id(child), CONTENT_RELATION)
      return child
