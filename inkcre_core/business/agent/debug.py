"""Opt-in development traces through the existing logging backend."""

import asyncio
import datetime
import json
import sys
import typing
import uuid

import pydantic

from inkcre_core.context import current_core
from inkcre_core.libs.obsrv.log_record import ENABLE_LOG_BACKEND, TRACE_ID
from inkcre_core.libs.obsrv.main import get_logger


def _json_value(value: typing.Any) -> typing.Any:
  if isinstance(value, pydantic.BaseModel):
    return value.model_dump(mode="python")
  if isinstance(value, bytes):
    return {"omitted_binary_bytes": len(value)}
  if isinstance(value, datetime.datetime | datetime.date):
    return value.isoformat()
  if isinstance(value, uuid.UUID):
    return str(value)
  raise TypeError(f"Unsupported Agent trace value: {type(value).__name__}")


def _emit(event: str, thread_id: str, payload: dict[str, typing.Any]) -> None:
  token = ENABLE_LOG_BACKEND.set(True)
  try:
    envelope = {
      "event": event,
      "thread_id": thread_id,
      "trace_id": TRACE_ID.get(),
      **payload,
    }
    get_logger().getChild("agent.debug").info(
      json.dumps(envelope, ensure_ascii=False, default=_json_value),
      extra={"event": event, "agent_thread_id": thread_id},
    )
  except Exception as error:
    # A debug destination must never replace the Agent's real outcome.
    sys.stderr.write(f"Agent debug trace unavailable: {type(error).__name__}\n")
  finally:
    ENABLE_LOG_BACKEND.reset(token)


async def trace(event: str, thread_id: uuid.UUID, **payload: typing.Any) -> None:
  if current_core().options.agent_debug:
    await asyncio.to_thread(_emit, event, str(thread_id), payload)
