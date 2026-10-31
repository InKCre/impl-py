"""Mail connection setup preserves progress committed by concurrent Jobs."""

import asyncio
import datetime
import os
from unittest import mock

import pytest

from app.business.source import SourceManager
from app.persistence.source.uow import source_uow
from app.schemas.job import JobModel
from app.schemas.source import SourceModel
from extensions.mail.schema import (
  IMAPCheckpoint,
  MailAccessBinding,
  MailBackfillConfig,
  MailCollectConfig,
  MailSourceState,
)
from extensions.mail.source import MailSourceBindingError, Source
from tests.database import TestSession


pytestmark = [
  pytest.mark.integration,
  pytest.mark.skipif(
    not os.getenv("INKCRE_TEST_DATABASE_URL"),
    reason="requires an explicitly selected migrated disposable PostgreSQL runtime",
  ),
]

BINDING = MailAccessBinding(
  protocol="imap", host="mail.invalid", port=993, security="tls", username="reader"
)


@pytest.fixture
def source_id(async_runner):
  source_type = f"{Source.__module__}.{Source.__qualname__}"
  async_runner.run(SourceManager.sync_source_types_async({source_type: Source}))
  source = async_runner.run(
    SourceManager.create(
      source_type,
      config={
        "parameters": {"host": "mail.invalid", "username": "reader", "password": "test"},
        "excluded_mailboxes": {"names": [], "special_uses": []},
      },
    )
  )
  yield source.id
  with TestSession() as session:
    session.delete(session.get(SourceModel, source.id))
    session.commit()


@pytest.mark.parametrize("command", ["collect", "backfill"])
@pytest.mark.parametrize("already_bound", [False, True])
def test_connecting_job_preserves_concurrent_checkpoint(
  async_runner, source_id, command, already_bound
):
  async def exercise():
    connecting, release_connection = asyncio.Event(), asyncio.Event()
    before = MailSourceState(binding=BINDING if already_bound else None)
    after = before.model_copy(
      update={"checkpoints": {"123": IMAPCheckpoint(uid_validity=7, last_uid=900)}}
    )

    class Adapter:
      binding = BINDING

      async def __aenter__(self):
        connecting.set()
        await release_connection.wait()
        return self

      async def __aexit__(self, *_exc):
        pass

      async def discover_mailboxes(self, _exclusions):
        return []

    async with source_uow() as uow:
      row = await uow.sources.get(source_id, lock=True)
      assert row is not None
      row.state = before.model_dump(mode="json")
      await uow.sources.save(row)
    config = (
      MailCollectConfig()
      if command == "collect"
      else MailBackfillConfig(since=datetime.date(2020, 1, 1))
    )
    with mock.patch("extensions.mail.source.create_mail_adapter", return_value=Adapter()):
      pending = asyncio.create_task(
        getattr(Source(source_id), command)(
          JobModel(type=f"core.source.{command}.v1", parameters={}), config
        )
      )
      try:
        await asyncio.wait_for(connecting.wait(), timeout=5)
        async with source_uow() as uow:
          row = await uow.sources.get(source_id, lock=True)
          assert row is not None
          row.state = after.model_dump(mode="json")
          await uow.sources.save(row)
      finally:
        release_connection.set()
        await pending
    async with source_uow() as uow:
      row = await uow.sources.get(source_id)
      assert row is not None
      state = MailSourceState.model_validate(row.state)
      assert state.binding == BINDING
      assert state.checkpoints == after.checkpoints

  async_runner.run(exercise())


def test_changed_remote_binding_fails_without_overwriting_state(async_runner, source_id):
  state = MailSourceState(
    binding=BINDING,
    checkpoints={"123": IMAPCheckpoint(uid_validity=7, last_uid=900)},
  )

  async def exercise():
    async with source_uow() as uow:
      row = await uow.sources.get(source_id, lock=True)
      assert row is not None
      row.state = state.model_dump(mode="json")
      await uow.sources.save(row)
    adapter = mock.AsyncMock()
    adapter.__aenter__.return_value = adapter
    adapter.binding = BINDING.model_copy(update={"username": "different-account"})
    with mock.patch("extensions.mail.source.create_mail_adapter", return_value=adapter):
      with pytest.raises(MailSourceBindingError, match="access binding changed"):
        await Source(source_id).collect(
          JobModel(type="core.source.collect.v1", parameters={}), MailCollectConfig()
        )
    async with source_uow() as uow:
      row = await uow.sources.get(source_id)
      assert row is not None
      assert MailSourceState.model_validate(row.state) == state
    adapter.discover_mailboxes.assert_not_called()

  async_runner.run(exercise())
