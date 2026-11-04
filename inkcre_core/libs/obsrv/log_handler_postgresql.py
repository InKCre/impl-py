"""Bounded bridge from synchronous logging calls to async PostgreSQL writes."""

import asyncio
import logging
from queue import Empty, Full, Queue
import sys

from inkcre_core.libs.obsrv.log_record import ENABLE_LOG_BACKEND, LogModel, adapt_log_record


class PostgreSQLHandler(logging.Handler):
  """Capture caller context immediately; persist only while lifespan is running."""

  def __init__(self, level: int):
    super().__init__(level)
    self._queue: Queue[LogModel] = Queue(maxsize=1024)
    self._task: asyncio.Task[None] | None = None
    self._accepting = False
    self.dropped = 0

  def start(self) -> None:
    if self._task is not None:
      raise RuntimeError("PostgreSQL log writer is already running")
    self._accepting = True
    self._task = asyncio.create_task(self._write(), name="postgresql-log-writer")

  def emit(self, record: logging.LogRecord) -> None:
    if not self._accepting or not ENABLE_LOG_BACKEND.get():
      return
    try:
      self._queue.put_nowait(adapt_log_record(record))
    except Full:
      self.dropped += 1
      if self.dropped == 1:
        print("PostgreSQL log queue full; dropping backend records", file=sys.stderr)
    except Exception:
      self.handleError(record)

  async def _write(self) -> None:
    from inkcre_core.persistence.observability.uow import write_logs

    while self._accepting or not self._queue.empty():
      batch: list[LogModel] = []
      for _ in range(100):
        try:
          batch.append(self._queue.get_nowait())
        except Empty:
          break
      if not batch:
        await asyncio.sleep(0.1)
        continue
      try:
        await write_logs(batch)
      except Exception as error:
        # Report through stderr, never recursively through the failed backend.
        print(f"PostgreSQL log batch failed: {type(error).__name__}", file=sys.stderr)

  async def aclose(self) -> None:
    # Handler.handle() holds this lock even when emit originates on a worker thread.
    self.acquire()
    try:
      self._accepting = False
    finally:
      self.release()
    if self._task is not None:
      try:
        async with asyncio.timeout(5):
          await self._task
      except TimeoutError:
        print("PostgreSQL log drain timed out; pending records lost", file=sys.stderr)
      finally:
        self._task = None
        while not self._queue.empty():
          self._queue.get_nowait()
    super().close()
