"""One protocol-neutral Mail Source backed by the selected public adapter."""

from __future__ import annotations

import typing

import pydantic

from app.business.source import SourceBase
from app.persistence.source.uow import source_uow
from app.schemas.job import JobModel
from app.schemas.source import SourceModel
from libs.obsrv.main import get_logger

from .adapter import create_mail_adapter
from .reconcile import MailGraphReconciler
from .schema import (
  IMAPCheckpoint,
  MailAccessBinding,
  MailBackfillConfig,
  MailCollectConfig,
  MailboxExclusionPolicy,
  MailSourceConfig,
  MailSourceState,
)


LOGGER = get_logger().getChild(__name__)


class MailSourceBindingError(RuntimeError):
  """A configured Source no longer points at its accepted access context."""


async def _mail_extension_default_exclusions() -> MailboxExclusionPolicy:
  """Read current extension defaults without requiring a running API mount."""
  from app.business.extension import EXTENSION_HOST
  from . import Extension

  running = EXTENSION_HOST.running.get("inkcre/mail")
  if running is not None:
    extension_class = typing.cast(type[Extension], running.extension_class)
    return extension_class.config.default_excluded_mailboxes
  persisted = await EXTENSION_HOST.store.get("inkcre/mail")
  config = Extension.validate_config({} if persisted is None else persisted.config)
  return config.default_excluded_mailboxes


class Source(
  SourceBase[MailSourceConfig],
  config_cls=MailSourceConfig,
  collect_config_cls=MailCollectConfig,
  backfill_config_cls=MailBackfillConfig,
):
  """Collect Mail through one configured public protocol access context."""

  source_type = "extensions.mail.source.Source"

  async def collect(self, job: JobModel, config: pydantic.BaseModel) -> None:
    source, setup = await self._load_effective_source()
    diagnostics: list[dict[str, typing.Any]] = []
    counts = {"messages": 0, "flag_changes": 0, "removals": 0, "mailboxes": 0}
    job.state = {"diagnostics": diagnostics, "counts": counts}

    adapter = create_mail_adapter(setup.protocol, setup.parameters)
    async with adapter:
      state = await self._accept_binding(adapter.binding)
      exclusions = setup.excluded_mailboxes
      if exclusions is None:  # pragma: no cover - materialization postcondition
        raise RuntimeError("Mail Source exclusions were not materialized")
      mailboxes = await adapter.discover_mailboxes(exclusions)
      for mailbox_fact in mailboxes:
        try:
          mailbox_id = await self._ensure_mailbox(mailbox_fact, state)
          checkpoint = state.checkpoints.get(str(mailbox_id))
          changes = await adapter.read_ordinary_changes(
            mailbox_fact,
            checkpoint,
            source.created_at,
          )
          for message in changes.messages:
            await self._persist_message(mailbox_id, message)
            counts["messages"] += 1
            if setup.ordinary_mark_as_seen and "\\seen" not in {
              flag.casefold() for flag in message.flags
            }:
              try:
                await adapter.mark_seen(mailbox_fact.mailbox.name, message.uid)
              except Exception as error:
                diagnostics.append(
                  {
                    "scope": "seen",
                    "mailbox": mailbox_fact.mailbox.name,
                    "uid": message.uid,
                    "message": str(error),
                  }
                )
              else:
                await self._persist_seen(mailbox_id, message)
          for change in changes.flag_changes:
            if await self._persist_flag_change(mailbox_id, change):
              counts["flag_changes"] += 1
          if setup.synchronize_deletions:
            for uid in changes.removed_uids:
              if await self._remove_occurrence(
                mailbox_id,
                changes.next_checkpoint.uid_validity,
                uid,
              ):
                counts["removals"] += 1
          await self._merge_checkpoint(mailbox_id, checkpoint, changes.next_checkpoint)
          state.checkpoints[str(mailbox_id)] = changes.next_checkpoint
          counts["mailboxes"] += 1
        except Exception as error:
          LOGGER.exception(
            "Mail mailbox collection failed",
            extra={"source": self._id, "mailbox": mailbox_fact.mailbox.name},
          )
          diagnostics.append(
            {
              "scope": "mailbox",
              "mailbox": mailbox_fact.mailbox.name,
              "message": str(error),
            }
          )

  async def backfill(self, job: JobModel, config: pydantic.BaseModel) -> None:
    interval = typing.cast(MailBackfillConfig, config)
    _source, setup = await self._load_effective_source()
    diagnostics: list[dict[str, typing.Any]] = []
    count = 0
    job.state = {"diagnostics": diagnostics, "messages": count}

    adapter = create_mail_adapter(setup.protocol, setup.parameters)
    async with adapter:
      state = await self._accept_binding(adapter.binding)
      exclusions = setup.excluded_mailboxes
      if exclusions is None:  # pragma: no cover - materialization postcondition
        raise RuntimeError("Mail Source exclusions were not materialized")
      for mailbox_fact in await adapter.discover_mailboxes(exclusions):
        try:
          mailbox_id = await self._ensure_mailbox(mailbox_fact, state)
          for message in await adapter.read_backfill(mailbox_fact, interval):
            await self._persist_message(mailbox_id, message)
            count += 1
            if setup.backfill_mark_as_seen and "\\seen" not in {
              flag.casefold() for flag in message.flags
            }:
              try:
                await adapter.mark_seen(mailbox_fact.mailbox.name, message.uid)
              except Exception as error:
                diagnostics.append(
                  {
                    "scope": "seen",
                    "mailbox": mailbox_fact.mailbox.name,
                    "uid": message.uid,
                    "message": str(error),
                  }
                )
              else:
                await self._persist_seen(mailbox_id, message)
        except Exception as error:
          LOGGER.exception(
            "Mail mailbox backfill failed",
            extra={"source": self._id, "mailbox": mailbox_fact.mailbox.name},
          )
          diagnostics.append(
            {
              "scope": "mailbox",
              "mailbox": mailbox_fact.mailbox.name,
              "message": str(error),
            }
          )
    job.state["messages"] = count

  async def _load_effective_source(self) -> tuple[SourceModel, MailSourceConfig]:
    """Materialize inherited exclusions without retaining a DB scope across lookup."""

    async with source_uow() as uow:
      source = await uow.sources.get(self._id)
      if source is None:
        raise MailSourceBindingError("Mail Source no longer exists")
      config = MailSourceConfig.model_validate(source.config)
    if config.excluded_mailboxes is not None:
      return source, config
    defaults = await _mail_extension_default_exclusions()
    async with source_uow() as uow:
      source = await uow.sources.get(self._id, lock=True)
      if source is None:
        raise MailSourceBindingError("Mail Source no longer exists")
      config = MailSourceConfig.model_validate(source.config)
      if config.excluded_mailboxes is None:
        config = config.model_copy(update={"excluded_mailboxes": defaults})
        source.config = config.model_dump(mode="json")
        await uow.sources.save(source)
      return source, config

  async def _accept_binding(self, binding: MailAccessBinding) -> MailSourceState:
    """Accept the remote context without replacing another Job's progress."""
    async with source_uow() as uow:
      source = await uow.sources.get(self._id, lock=True)
      if source is None:
        raise MailSourceBindingError("Mail Source no longer exists")
      # Connecting can outlive another collect/backfill Job. Read checkpoints
      # under the same row lock that protects their compare-and-swap updates.
      state = MailSourceState.model_validate(source.state or {})
      if state.binding is not None and state.binding != binding:
        raise MailSourceBindingError(
          "Mail Source access binding changed; create a new Source for a different context"
        )
      if state.binding is None:
        state.binding = binding
        source.state = state.model_dump(mode="json")
        await uow.sources.save(source)
      return state

  async def _ensure_mailbox(self, fact, state: MailSourceState) -> int:
    async with source_uow() as uow:
      source = await uow.sources.get(self._id)
      if source is None:  # pragma: no cover - Job eligibility invariant
        raise ValueError("Mail Source no longer exists")
      reconciler = await MailGraphReconciler.for_source(uow, source)
      mailbox = await reconciler.ensure_mailbox(fact)
      if mailbox.id is None:
        raise RuntimeError("Persisted Mailbox has no ID")
      previous = state.checkpoints.get(str(mailbox.id))
      if previous is not None and previous.uid_validity != fact.uid_validity:
        await reconciler.clear_stale_epoch(mailbox.id, fact.uid_validity)
      return mailbox.id

  async def _persist_message(self, mailbox_id: int, fact) -> None:
    async with source_uow() as uow:
      source = await uow.sources.get(self._id)
      mailbox = await uow.graph.blocks.get(mailbox_id)
      if source is None or mailbox is None:
        raise ValueError("Mail graph provenance is no longer available")
      reconciler = await MailGraphReconciler.for_source(uow, source)
      await reconciler.reconcile_message(mailbox, fact)

  async def _persist_seen(self, mailbox_id: int, fact) -> None:
    flags = tuple((*fact.flags, "\\Seen"))
    async with source_uow() as uow:
      source = await uow.sources.get(self._id)
      mailbox = await uow.graph.blocks.get(mailbox_id)
      if source is None or mailbox is None:
        return
      reconciler = await MailGraphReconciler.for_source(uow, source)
      await reconciler.reconcile_flag_change(
        mailbox,
        fact.uid_validity,
        fact.uid,
        flags,
      )

  async def _persist_flag_change(self, mailbox_id: int, change) -> bool:
    async with source_uow() as uow:
      source = await uow.sources.get(self._id)
      mailbox = await uow.graph.blocks.get(mailbox_id)
      if source is None or mailbox is None:
        return False
      reconciler = await MailGraphReconciler.for_source(uow, source)
      changed = await reconciler.reconcile_flag_change(
        mailbox,
        change.uid_validity,
        change.uid,
        change.flags,
      )
      return changed

  async def _remove_occurrence(self, mailbox_id: int, uid_validity: int, uid: int) -> bool:
    async with source_uow() as uow:
      source = await uow.sources.get(self._id)
      mailbox = await uow.graph.blocks.get(mailbox_id)
      if source is None or mailbox is None:
        return False
      reconciler = await MailGraphReconciler.for_source(uow, source)
      removed = await reconciler.remove_occurrence(
        mailbox,
        uid_validity,
        uid,
      )
      return removed

  async def _merge_checkpoint(
    self,
    mailbox_id: int,
    observed: IMAPCheckpoint | None,
    proposed: IMAPCheckpoint,
  ) -> None:
    """Advance one mailbox only when its persisted base is still observed."""
    async with source_uow() as uow:
      source = await uow.sources.get(self._id, lock=True)
      if source is None:
        raise MailSourceBindingError("Mail Source no longer exists")
      state = MailSourceState.model_validate(source.state or {})
      current = state.checkpoints.get(str(mailbox_id))
      if current != observed:
        return
      state.checkpoints[str(mailbox_id)] = proposed
      source.state = state.model_dump(mode="json")
      await uow.sources.save(source)
