"""Consume Telegram updates only after their graph and bot-scoped cursor commit."""

import json
import os
from uuid import uuid4

import pytest
import sqlmodel
from telegram.request import HTTPXRequest

from extensions.telegram import Extension
from extensions.telegram.source import Source
from inkcre_core.business.info_base.resolver import ResolverManager
from inkcre_core.business.source import SourceManager
from inkcre_core.persistence.source.repository import SourceRepository
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.job import JobStatus
from inkcre_core.schemas.source import SourceModel
from tests.database import TestSession


def test_published_message_v1_still_reads_from_its_stored_json(async_runner):
  Extension.load_decoders()
  saved = BlockModel(
    id=123,
    resolver="extensions.telegram.message.v1",
    content='{"message_id":7,"date":"2024-01-05T00:00:00Z","caption":"旧照片","has_media":true,"media_type":"photo"}',
  )
  reader = ResolverManager.get(saved)
  assert async_runner.run(reader.get_text()) == "旧照片\n[photo]"


@pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires a migrated disposable database",
)
def test_failed_primary_retries_from_committed_offset_before_success_reaction(
  async_runner, monkeypatch, run_source_job
):
  token = uuid4().hex
  allowed, denied = f"admitted-{token}", f"other-sender-{token}"
  source_type = f"{Source.__module__}.{Source.__name__}"
  async_runner.run(SourceManager.sync_source_types_async())
  source = async_runner.run(
    SourceManager.create(
      source_type, config={"bot_token": "123:fixture", "bound_user_id": 7}
    )
  )
  assert source.id is not None
  source_id = source.id
  offsets = []
  notifications = []

  def durable_status():
    with TestSession() as db:
      state = db.get_one(SourceModel, source_id).state
      texts = db.exec(
        sqlmodel.select(BlockModel.content).where(
          sqlmodel.col(BlockModel.content).in_([allowed, denied])
        )
      ).all()
      return state, list(texts)

  def message(update_id, sender, text):
    return {
      "message_id": update_id,
      "date": 1760000000,
      "text": text,
      "chat": {"id": sender, "type": "private"},
      "from": {"id": sender, "is_bot": False, "first_name": "Reader"},
    }

  async def bot_api(_request, url, method, request_data=None, **_timeouts):
    endpoint = url.rpartition("/")[-1]
    params = request_data.parameters if request_data else {}
    if endpoint == "getMe":
      result = {"id": 123, "is_bot": True, "first_name": "Test", "username": "fixture_bot"}
    elif endpoint == "getUpdates":
      offset = params.get("offset")
      offsets.append(offset)
      result = [
        {"update_id": index, "message": message(index, sender, text)}
        for index, sender, text in ((8, 11, denied), (9, 7, allowed))
        if offset is None or index >= offset
      ]
    elif endpoint == "sendMessage":
      notifications.append((params, durable_status()))
      result = message(90, 7, params["text"])
    elif endpoint == "setMessageReaction":
      notifications.append((params, durable_status()))
      result = True
    else:
      raise AssertionError(f"unexpected Telegram method {endpoint}")
    return 200, json.dumps({"ok": True, "result": result}).encode()

  monkeypatch.setattr(HTTPXRequest, "do_request", bot_api)
  save = SourceRepository.save

  async def fail_cursor_write(repo, row):
    if row.id == source_id and row.state.get("last_update_id") == 9:
      raise RuntimeError("fixture cursor write interrupted")
    await save(repo, row)

  try:
    with monkeypatch.context() as fault:
      fault.setattr(SourceRepository, "save", fail_cursor_write)
      failed = async_runner.run(run_source_job(source_id))
    assert failed.status == JobStatus.FAILED
    assert failed.state["error"] == "fixture cursor write interrupted"
    failed_state = ({"bot_id": 123, "last_update_id": 8}, [])
    assert durable_status() == failed_state
    (notice, at_failure) = notifications.pop()
    assert at_failure == failed_state and "will be retried" in notice["text"]
    assert notice["chat_id"] == 7

    succeeded = async_runner.run(run_source_job(source_id))
    assert succeeded.status == JobStatus.FINISHED, succeeded.state
    success_state = ({"bot_id": 123, "last_update_id": 9}, [allowed])
    (reaction, at_ack) = notifications.pop()
    assert reaction["message_id"] == 9 and reaction["chat_id"] == 7
    assert reaction["reaction"] == [{"type": "emoji", "emoji": "👍"}]
    assert at_ack == success_state

    empty = async_runner.run(run_source_job(source_id))
    assert empty.status == JobStatus.FINISHED
    assert offsets == [None, 9, 10]
    assert durable_status() == success_state and not notifications
  finally:
    async_runner.run(SourceManager.delete(source_id))
    with TestSession() as db:
      db.connection().execute(
        sqlmodel.delete(BlockModel).where(
          sqlmodel.col(BlockModel.content).in_([allowed, denied])
        )
      )
      db.commit()
