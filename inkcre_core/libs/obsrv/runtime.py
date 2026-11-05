"""Instance-owned recording with bounded queues and a selected query backend."""

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
import logging
import uuid
from queue import Empty, Full, Queue
from threading import Lock
from typing import Any

from opentelemetry import trace

from .backends import ObservationBackend, ObservabilityUnavailable
from .log_record import TRACE_ID, SPAN_ID, ENABLE_LOG_BACKEND, adapt_log_record
from .records import (
  BackendName,
  RecordKind,
  ObservationQuery,
  ObservationRecord,
  ObservationStatus,
)

# Raw content has a separate permission from selecting an external destination.
_METADATA = frozenset(
  {
    "inkcre.peer.id",
    "inkcre.job.id",
    "inkcre.job.status",
    "inkcre.cron.id",
    "inkcre.operation",
    "inkcre.outcome",
    "inkcre.thread.id",
    "inkcre.agent.id",
    "inkcre.tool.id",
    "inkcre.tool_call.id",
    "inkcre.resolver.id",
    "inkcre.block.id",
    "inkcre.peer.target_id",
    "error.type",
    "reason",
    "outcome",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "operation",
    "job_id",
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "http.request.method",
    "http.response.status_code",
    "http.route",
    "gen_ai.conversation.id",
    "gen_ai.tool.name",
    "inkcre.agent.model_call",
    "inkcre.agent.model_calls",
    "inkcre.agent.outcome",
    "inkcre.agent.tool_call.id_hash",
    "inkcre.agent.turn",
    "inkcre.ai.model.id",
    "inkcre.ai.usage.input.source",
    "inkcre.ai.usage.output.source",
    "inkcre.entity.block_ids",
    "inkcre.entity.read",
    "inkcre.entity.read.count",
    "inkcre.entity.read.kind",
    "inkcre.entity.relation_ids",
    "inkcre.retrieval.candidate_count",
    "inkcre.retrieval.candidates",
    "inkcre.retrieval.method",
  }
)


def current_observability() -> "ObservationRuntime | None":
  from inkcre_core.context import _CURRENT

  core = _CURRENT.get(None)
  return core.observability if core is not None else None


class ObservationRuntime:
  def __init__(
    self,
    backend: ObservationBackend | None,
    *,
    selected: BackendName,
    peer_id: str,
    error: str | None = None,
    allow_external_content: bool = False,
  ):
    self.backend = backend
    self.selected = selected
    self.peer_id = peer_id
    self.error = error
    self.allow_external_content = allow_external_content
    self.queue: Queue[ObservationRecord] = Queue(maxsize=1024)
    self._lock = Lock()
    self.dropped = 0
    self._closed = False
    self.requests_in_flight = 0
    self._queries = 0
    self._accepting = False
    self._task: asyncio.Task | None = None

  def start(self) -> None:
    if self._closed or self._task is not None:
      raise RuntimeError("Observability runtime is closed or already started")
    if self.backend is not None:
      self._accepting = True
      self._task = asyncio.create_task(self._write(), name="observability-writer")

  def status(self) -> ObservationStatus:
    return ObservationStatus(
      backend=self.selected,
      configured=self.backend is not None,
      recording=self._accepting,
      queryable=self.backend is not None and not self._closed,
      dropped=self.dropped,
      error=self.error,
    )

  def emit(self, record: ObservationRecord) -> bool:
    if not self._accepting:
      self.dropped += 1
      return False
    attributes = {**record.attributes}
    if "inkcre.peer.id" not in attributes and len(attributes) == 64:
      self.dropped += 1
      self.error = "No metadata capacity for the recording Peer identity"
      return False
    attributes.setdefault("inkcre.peer.id", self.peer_id)
    if self.selected == "loki" and not self.allow_external_content:
      attributes = {key: value for key, value in attributes.items() if key in _METADATA}
      record = record.model_copy(
        update={
          "body": record.event_name
          if record.kind != "log" and record.event_name
          else "application.log",
          "attributes": attributes,
        }
      )
    else:
      record = record.model_copy(update={"attributes": attributes})
    with self._lock:
      if not self._accepting:
        self.dropped += 1
        return False
      try:
        self.queue.put_nowait(record)
      except Full:
        self.dropped += 1
        self.error = "Recording queue full; records were dropped"
        return False
    return True

  async def _write(self) -> None:
    assert self.backend is not None
    while self._accepting or not self.queue.empty():
      batch = []
      for _ in range(100):
        try:
          batch.append(self.queue.get_nowait())
        except Empty:
          break
      if not batch:
        await asyncio.sleep(0.1)
        continue
      try:
        await self.backend.write(batch)
      except Exception:
        # No recursive logging, credential-bearing errors, retry or fallback store.
        self.dropped += len(batch)
        self.error = "Selected backend write failed; records were dropped"

  async def query(self, query: ObservationQuery):
    if self._closed or self.backend is None:
      raise ObservabilityUnavailable("Selected observability backend is not configured")
    if self._queries >= 4:
      raise ObservabilityUnavailable("Observability query capacity reached")
    self._queries += 1
    try:
      async with asyncio.timeout(10):
        return await self.backend.query(query)
    except Exception as error:
      self.error = "Selected backend query failed"
      raise ObservabilityUnavailable(self.error) from error
    finally:
      self._queries -= 1

  async def aclose(self) -> None:
    if self._closed:
      return
    self._closed = True
    with self._lock:
      self._accepting = False
    if self._task is not None:
      try:
        async with asyncio.timeout(5):
          await self._task
      except TimeoutError:
        self.error = "Recording drain timed out; pending records were dropped"
      finally:
        self._task = None
    while not self.queue.empty():
      self.queue.get_nowait()
      self.dropped += 1
    if self.backend is not None:
      try:
        async with asyncio.timeout(2):
          await self.backend.aclose()
      except Exception:
        self.error = "Selected backend shutdown did not complete"


def record_event(
  name: str, attributes: Mapping[str, Any], *, kind: RecordKind = "event"
) -> None:
  runtime = current_observability()
  if runtime is None:
    return
  span = trace.get_current_span().get_span_context()
  failure = attributes.get("outcome") in {"error", "timeout"} or attributes.get(
    "inkcre.job.status"
  ) in {
    "failed",
    "timed_out",
  }
  severity = 17 if failure else 9
  severity_text = "ERROR" if failure else "INFO"
  try:
    runtime.emit(
      ObservationRecord(
        kind=kind,
        event_name=name,
        body=name,
        severity_number=severity,
        severity_text=severity_text,
        trace_id=f"{span.trace_id:032x}" if span.is_valid else TRACE_ID.get(),
        span_id=f"{span.span_id:016x}" if span.is_valid else SPAN_ID.get(),
        attributes=dict(attributes),
      )
    )
  except (ValueError, TypeError):
    runtime.dropped += 1
    runtime.error = "Invalid observation metadata was dropped"


class ObservationLogHandler(logging.Handler):
  """Resolve the caller's Core at emit time; never capture another instance's sink."""

  def emit(self, record: logging.LogRecord) -> None:
    runtime = current_observability()
    if runtime is None or not ENABLE_LOG_BACKEND.get():
      return
    try:
      adapted = adapt_log_record(record)
      runtime.emit(
        ObservationRecord(
          id=str(uuid.uuid4()),
          timestamp=datetime.fromtimestamp(record.created, timezone.utc),
          kind="log",
          event_name=None,
          severity_number=adapted.severity_number,
          severity_text=adapted.severity_text,
          body=adapted.body,
          trace_id=adapted.trace_id,
          span_id=adapted.span_id,
          attributes=adapted.attributes,
        )
      )
    except Exception:
      runtime.dropped += 1
      runtime.error = "Application log could not be adapted"
