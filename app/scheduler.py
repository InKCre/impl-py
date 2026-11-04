__all__ = ["scheduler", "with_trace_id"]

import asyncio
from app.core import CORE

import apscheduler.schedulers.asyncio

from inkcre_core.libs.obsrv.log_record import ENABLE_LOG_BACKEND, TRACE_ID

scheduler = apscheduler.schedulers.asyncio.AsyncIOScheduler()

_accepting = False
_running: set[asyncio.Task] = set()


def start_scheduler() -> None:
  global _accepting
  _accepting = True
  if not scheduler.running:
    scheduler.start()


async def drain_scheduler() -> None:
  """Stop new callbacks and await cancellation cleanup before disposing resources."""
  global _accepting
  _accepting = False
  if scheduler.running:
    scheduler.pause()
  tasks = tuple(_running)
  for task in tasks:
    if not task.cancelling():
      task.cancel()
  await asyncio.gather(*tasks, return_exceptions=True)


def with_trace_id(trace_id: str, coro, enable_backend: bool = True):
  async def wrapper(*args, **kwargs):
    if not _accepting:
      return
    task = asyncio.current_task()
    assert task is not None
    _running.add(task)
    token = TRACE_ID.set(trace_id)
    token_backend = ENABLE_LOG_BACKEND.set(enable_backend)
    try:
      with CORE.bind():
        return await coro(*args, **kwargs)
    finally:
      _running.discard(task)
      TRACE_ID.reset(token)
      ENABLE_LOG_BACKEND.reset(token_backend)

  return wrapper
