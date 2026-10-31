"""Bound private Telegram inbox collection through ordinary Source Jobs."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import typing

import pydantic
import telegram

from app.business.info_base.services import BlockService
from app.business.source import SourceBase, SourceManager
from app.persistence.source.uow import source_uow
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.job import JobModel
from libs.obsrv.main import get_logger

from .resolver import SOURCE_RELATION, TelegramAttachmentResolver
from .schema import (
  AttachmentKind,
  TelegramAttachment,
  TelegramSourceConfig,
  TelegramSourceState,
)


LOGGER = get_logger().getChild(__name__)
MAX_DIAGNOSTICS = 50


@dataclass(frozen=True)
class PersistedUpdate:
  status: typing.Literal["saved", "unsupported", "duplicate"]
  attachment_id: int | None = None


def _block_id(block: BlockModel) -> int:
  if block.id is None:
    raise RuntimeError("persisted Block has no ID")
  return block.id


def _duration(value: typing.Any) -> int | None:
  if isinstance(value, datetime.timedelta):
    return int(value.total_seconds())
  return value if isinstance(value, int) else None


def _attachment(message: telegram.Message) -> TelegramAttachment | None:
  kind: AttachmentKind
  value: typing.Any
  if message.photo:
    kind, value = "photo", max(message.photo, key=lambda photo: photo.width * photo.height)
  elif message.animation is not None:
    kind, value = "animation", message.animation
  else:
    candidates: tuple[tuple[AttachmentKind, typing.Any], ...] = (
      ("audio", message.audio),
      ("document", message.document),
      ("sticker", message.sticker),
      ("video", message.video),
      ("video_note", message.video_note),
      ("voice", message.voice),
    )
    selected = next(((name, item) for name, item in candidates if item is not None), None)
    if selected is None:
      return None
    kind, value = selected
  return TelegramAttachment(
    kind=kind,
    file_id=value.file_id,
    file_unique_id=value.file_unique_id,
    filename=getattr(value, "file_name", None),
    mime_type=getattr(value, "mime_type", None),
    file_size=getattr(value, "file_size", None),
    width=getattr(value, "width", None),
    height=getattr(value, "height", None),
    duration=_duration(getattr(value, "duration", None)),
    title=getattr(value, "title", None),
    performer=getattr(value, "performer", None),
    emoji=getattr(value, "emoji", None),
  )


def _semantic_text(message: telegram.Message, *, caption: bool = False) -> BlockForm | None:
  value = message.caption if caption else message.text
  if value is None:
    return None
  entities = message.caption_entities if caption else message.entities
  if entities:
    html = message.caption_html if caption else message.text_html
    return BlockForm(resolver="core.html.v1", content=html)
  return BlockForm(resolver="core.text.v1", content=value)


class Source(
  SourceBase[TelegramSourceConfig],
  config_cls=TelegramSourceConfig,
):
  """Collect useful content sent privately by one configured Telegram identity."""

  source_type = "extensions.telegram.source.Source"

  async def _pin_bot(self, bot_id: int) -> TelegramSourceState:
    async with source_uow() as uow:
      source = await uow.sources.get(self._id, lock=True)
      if source is None:
        raise ValueError("Telegram Source no longer exists")
      state = TelegramSourceState.model_validate(source.state or {})
      if state.bot_id is not None and state.bot_id != bot_id:
        raise ValueError("Telegram Source token resolves to a different bot")
      if state.bot_id is None:
        state = state.model_copy(update={"bot_id": bot_id})
        source.state = state.model_dump(mode="json", exclude_none=True)
        await uow.sources.save(source)
      return state

  async def _persist_update(
    self,
    update_id: int,
    *,
    text: BlockForm | None = None,
    attachment: TelegramAttachment | None = None,
    caption: BlockForm | None = None,
  ) -> PersistedUpdate:
    async with source_uow() as uow:
      source = await uow.sources.get(self._id, lock=True)
      if source is None:
        raise ValueError("Telegram Source no longer exists")
      state = TelegramSourceState.model_validate(source.state or {})
      if state.last_update_id is not None and state.last_update_id >= update_id:
        return PersistedUpdate("duplicate")

      if text is not None:
        await uow.graph.blocks.create(text)
      attachment_id = None
      if attachment is not None:
        source_anchor = await SourceManager.ensure_block_async(source, uow)
        metadata = await uow.graph.blocks.create(
          TelegramAttachmentResolver.create_block(attachment)
        )
        attachment_id = _block_id(metadata)
        await uow.graph.relations.create(
          _block_id(source_anchor), attachment_id, SOURCE_RELATION
        )
        if caption is not None:
          caption_block = await uow.graph.blocks.create(caption)
          await uow.graph.relations.create(
            attachment_id, _block_id(caption_block), "caption"
          )

      state = state.model_copy(update={"last_update_id": update_id})
      source.state = state.model_dump(mode="json", exclude_none=True)
      await uow.sources.save(source)
      status = "saved" if text is not None or attachment is not None else "unsupported"
      return PersistedUpdate(status, attachment_id)

  @staticmethod
  def _diagnostic(
    diagnostics: list[dict[str, typing.Any]], update_id: int, scope: str, message: str
  ) -> None:
    if len(diagnostics) < MAX_DIAGNOSTICS:
      diagnostics.append({"update_id": update_id, "scope": scope, "message": message})

  async def _notify(
    self,
    bot: telegram.Bot,
    message: telegram.Message,
    outcome: str,
    diagnostics: list[dict[str, typing.Any]],
    update_id: int,
  ) -> None:
    try:
      if outcome in {"saved", "partial"}:
        await bot.set_message_reaction(
          message.chat_id,
          message.message_id,
          telegram.ReactionTypeEmoji("👍"),
        )
      if outcome == "start":
        await bot.send_message(
          message.chat_id,
          "Send or forward useful text and files here. Saved messages receive 👍.",
          reply_to_message_id=message.message_id,
        )
      elif outcome == "unsupported":
        await bot.send_message(
          message.chat_id,
          "This message has no supported content to save.",
          reply_to_message_id=message.message_id,
        )
      elif outcome == "partial":
        await bot.send_message(
          message.chat_id,
          "Attachment metadata was saved, but its bytes were not downloaded; "
          "retry materialization later.",
          reply_to_message_id=message.message_id,
        )
    except telegram.error.TelegramError as error:
      LOGGER.warning("Telegram completion notification failed", exc_info=True)
      self._diagnostic(diagnostics, update_id, "acknowledgement", str(error))

  async def collect(self, job: JobModel, config: pydantic.BaseModel) -> None:
    del config
    setup = await self.get_config()
    state = TelegramSourceState.model_validate(await self.get_state())
    counts = {
      "saved": 0,
      "partial": 0,
      "unsupported": 0,
      "unauthorized": 0,
      "duplicates": 0,
      "failed": 0,
    }
    diagnostics: list[dict[str, typing.Any]] = []
    job.state = {"counts": counts, "diagnostics": diagnostics}

    async with telegram.Bot(setup.bot_token) as bot:
      identity = await bot.get_me()
      state = await self._pin_bot(identity.id)
      while True:
        updates = await bot.get_updates(
          offset=None if state.last_update_id is None else state.last_update_id + 1,
          limit=100,
          timeout=0,
          allowed_updates=["message"],
        )
        for update in sorted(updates, key=lambda item: item.update_id):
          message = update.message
          if (
            message is None
            or message.chat.type != telegram.constants.ChatType.PRIVATE
            or message.from_user is None
            or message.from_user.id != setup.bound_user_id
          ):
            outcome = await self._persist_update(update.update_id)
            if outcome.status != "duplicate":
              counts["unauthorized" if message is not None else "unsupported"] += 1
            state = TelegramSourceState.model_validate(await self.get_state())
            continue

          try:
            if message.text == "/start":
              outcome = await self._persist_update(update.update_id)
              if outcome.status != "duplicate":
                await self._notify(bot, message, "start", diagnostics, update.update_id)
              state = TelegramSourceState.model_validate(await self.get_state())
              continue

            attachment = _attachment(message)
            text = _semantic_text(message) if attachment is None else None
            caption = (
              _semantic_text(message, caption=True) if attachment is not None else None
            )
            outcome = await self._persist_update(
              update.update_id, text=text, attachment=attachment, caption=caption
            )
            if outcome.status == "duplicate":
              counts["duplicates"] += 1
            elif outcome.status == "unsupported":
              counts["unsupported"] += 1
              await self._notify(bot, message, "unsupported", diagnostics, update.update_id)
            else:
              notification = "saved"
              if setup.download_attachments and outcome.attachment_id is not None:
                block = await BlockService.get(outcome.attachment_id)
                if block is None:
                  raise RuntimeError("persisted Telegram attachment disappeared")
                try:
                  await TelegramAttachmentResolver(block).materialize_content()
                except Exception as error:
                  LOGGER.warning("Telegram attachment enrichment failed", exc_info=True)
                  self._diagnostic(
                    diagnostics, update.update_id, "materialization", str(error)
                  )
                  counts["partial"] += 1
                  notification = "partial"
                else:
                  counts["saved"] += 1
              else:
                counts["saved"] += 1
              await self._notify(bot, message, notification, diagnostics, update.update_id)
            state = TelegramSourceState.model_validate(await self.get_state())
          except Exception as error:
            counts["failed"] += 1
            self._diagnostic(diagnostics, update.update_id, "primary", str(error))
            try:
              await bot.send_message(
                message.chat_id,
                "This message was not saved and will be retried by the next collection.",
                reply_to_message_id=message.message_id,
              )
            except telegram.error.TelegramError as notification_error:
              self._diagnostic(
                diagnostics,
                update.update_id,
                "failure_notification",
                str(notification_error),
              )
            raise
        if len(updates) < 100:
          break
