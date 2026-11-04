"""Opt-in OTLP metadata transport; callers own allowed field names and value sources."""

import asyncio
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import partial
import logging
import math
import os
import sys
import time
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit
import uuid

from opentelemetry.context import Context
from opentelemetry.trace import INVALID_SPAN, Link, Span, SpanKind, StatusCode, use_span
from opentelemetry.util.types import AttributeValue
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
  from opentelemetry.sdk._logs import LoggerProvider
  from opentelemetry.sdk.metrics import MeterProvider
  from opentelemetry.sdk.trace import TracerProvider
  from opentelemetry.metrics import Counter, Histogram
  from opentelemetry.trace import Tracer
  from opentelemetry._logs import Logger


class _ExportSettings(BaseSettings):
  model_config = SettingsConfigDict(extra="ignore")

  otel_exporter_otlp_traces_endpoint: str = ""
  otel_exporter_otlp_logs_endpoint: str = ""
  otel_exporter_otlp_metrics_endpoint: str = ""
  otel_exporter_otlp_headers: SecretStr = SecretStr("")
  otel_exporter_otlp_traces_headers: SecretStr = SecretStr("")
  otel_exporter_otlp_logs_headers: SecretStr = SecretStr("")
  otel_exporter_otlp_metrics_headers: SecretStr = SecretStr("")
  # Validate each signal separately, so a bad timeout does not disable siblings.
  otel_exporter_otlp_timeout: str = "10"
  otel_exporter_otlp_traces_timeout: str = ""
  otel_exporter_otlp_logs_timeout: str = ""
  otel_exporter_otlp_metrics_timeout: str = ""


@dataclass
class _Runtime:
  connections: dict[str, tuple[str, dict[str, str], float]] = field(default_factory=dict)
  traces: "TracerProvider | None" = None
  logs: "LoggerProvider | None" = None
  metrics: "MeterProvider | None" = None
  tracer: "Tracer | None" = None
  logger: "Logger | None" = None
  operations: "Counter | None" = None
  duration: "Histogram | None" = None
  tokens: "Counter | None" = None
  missing_usage: "Counter | None" = None


_runtime: _Runtime | None = None


def _diagnostic(reason: str) -> None:
  # This sink never receives endpoints, headers, exceptions, or arbitrary log messages.
  try:
    print(f"OpenTelemetry: {reason}", file=sys.stderr)
  except Exception:
    # Diagnostics cannot make a broken stderr stream a business failure.
    pass


class _SDKDiagnostics(logging.Handler):
  def emit(self, record: logging.LogRecord) -> None:
    # SDK errors may contain response bodies, URLs or malformed secret headers.
    _diagnostic("SDK warning or export failure; check the configured OTLP receiver")


def _configure_diagnostics() -> None:
  logger = logging.getLogger("opentelemetry")
  if not any(isinstance(handler, _SDKDiagnostics) for handler in logger.handlers):
    handler = _SDKDiagnostics(level=logging.WARNING)
    logger.addHandler(handler)
  logger.propagate = False


def _endpoint(value: str) -> str:
  parsed = urlsplit(value)
  if (
    parsed.scheme not in ("http", "https")
    or not parsed.hostname
    or not parsed.path
    or parsed.username is not None
    or parsed.password is not None
    or parsed.query
    or parsed.fragment
    or any(character.isspace() for character in value)
  ):
    raise ValueError("invalid OTLP URL")
  # Accessing port performs urllib's range and syntax validation.
  parsed.port
  return value


def is_enabled() -> bool:
  """Whether at least one metadata signal initialized for this runtime."""
  return _runtime is not None


def get_export_connection(signal: str) -> tuple[str, dict[str, str], float] | None:
  """Return private endpoint, copied headers and timeout seconds for server forwarding."""
  connection = _runtime.connections.get(signal) if _runtime is not None else None
  return (
    (connection[0], dict(connection[1]), connection[2]) if connection is not None else None
  )


def start_telemetry(
  *,
  enabled: bool,
  service_version: str,
  resource_attributes: Mapping[str, AttributeValue] | None = None,
  endpoints: Mapping[str, str] | None = None,
) -> None:
  """Initialize only configured signals; configuration failures never gate readiness."""
  global _runtime
  if not enabled or _runtime is not None:
    return

  _configure_diagnostics()
  try:
    config = _ExportSettings(_env_file=os.getenv("INKCRE_ENV_FILE", ".env") or None)
  except Exception:
    _diagnostic("invalid runtime export configuration; telemetry is disabled")
    return

  from opentelemetry.exporter.otlp.proto.http import Compression
  from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
  from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
  from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
  from opentelemetry.metrics import NoOpMeterProvider
  from opentelemetry.sdk._logs import LoggerProvider, LogRecordLimits
  from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
  from opentelemetry.sdk.metrics import MeterProvider, SimpleFixedSizeExemplarReservoir
  from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
  from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
  from opentelemetry.sdk.resources import Resource
  from opentelemetry.sdk.trace import SpanLimits, TracerProvider
  from opentelemetry.sdk.trace.export import BatchSpanProcessor
  from opentelemetry.sdk.trace.sampling import ALWAYS_ON
  from opentelemetry.util.re import parse_env_headers
  from requests.utils import check_header_validity  # type: ignore[untyped-import]

  # Resource.create invokes environment detectors; direct construction is deliberate.
  resource = Resource(
    {
      **(resource_attributes or {}),
      "service.name": "core-py",
      "service.version": service_version,
      "service.instance.id": str(uuid.uuid4()),
    }
  )
  runtime = _Runtime()
  no_metrics = NoOpMeterProvider()
  for signal in ("metrics", "traces", "logs"):
    endpoint = getattr(config, f"otel_exporter_otlp_{signal}_endpoint") or (
      endpoints or {}
    ).get(signal, "")
    if not endpoint:
      continue
    cleanup: Callable[[], None] | None = None
    internal_metrics = runtime.metrics or no_metrics
    try:
      endpoint = _endpoint(endpoint)
      timeout = float(
        getattr(config, f"otel_exporter_otlp_{signal}_timeout")
        or config.otel_exporter_otlp_timeout
        or "10"
      )
      if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("OTLP timeout must be finite, positive and at most 30 seconds")
      raw_headers = (
        getattr(config, f"otel_exporter_otlp_{signal}_headers").get_secret_value()
        or config.otel_exporter_otlp_headers.get_secret_value()
      )
      headers = parse_env_headers(raw_headers, liberal=True)
      # The SDK parser drops malformed entries. Do not silently accept partial auth.
      if len(headers) != len([part for part in raw_headers.split(",") if part.strip()]):
        raise ValueError("invalid OTLP headers")
      for header in headers.items():
        check_header_validity(header)

      if signal == "traces":
        exporter = OTLPSpanExporter(
          endpoint=endpoint,
          headers=headers,
          timeout=timeout,
          compression=Compression.NoCompression,
          meter_provider=internal_metrics,
        )
        cleanup = exporter.shutdown
        traces = TracerProvider(
          resource=resource,
          sampler=ALWAYS_ON,
          shutdown_on_exit=False,
          span_limits=SpanLimits(
            max_attributes=64,
            max_events=16,
            max_links=16,
            max_attribute_length=256,
            max_span_attributes=64,
            max_event_attributes=64,
            max_link_attributes=64,
            max_span_attribute_length=256,
          ),
          meter_provider=internal_metrics,
        )
        processor = BatchSpanProcessor(
          exporter,
          max_queue_size=256,
          max_export_batch_size=256,
          schedule_delay_millis=5000,
          export_timeout_millis=timeout * 1000,
          meter_provider=internal_metrics,
        )
        cleanup = processor.shutdown
        traces.add_span_processor(processor)
        cleanup = traces.shutdown
        tracer = traces.get_tracer("inkcre.metadata")
        runtime.traces, runtime.tracer = traces, tracer
      elif signal == "logs":
        log_exporter = OTLPLogExporter(
          endpoint=endpoint,
          headers=headers,
          timeout=timeout,
          compression=Compression.NoCompression,
          meter_provider=internal_metrics,
        )
        cleanup = log_exporter.shutdown
        logs = LoggerProvider(
          resource=resource,
          shutdown_on_exit=False,
          meter_provider=internal_metrics,
          log_record_limits=LogRecordLimits(max_attributes=64, max_attribute_length=256),
        )
        log_processor = BatchLogRecordProcessor(
          log_exporter,
          max_queue_size=256,
          max_export_batch_size=256,
          schedule_delay_millis=5000,
          export_timeout_millis=timeout * 1000,
          meter_provider=internal_metrics,
        )
        cleanup = log_processor.shutdown
        logs.add_log_record_processor(log_processor)
        cleanup = logs.shutdown
        logger = logs.get_logger("inkcre.metadata")
        runtime.logs, runtime.logger = logs, logger
      else:
        metric_exporter = OTLPMetricExporter(
          endpoint=endpoint,
          headers=headers,
          timeout=timeout,
          compression=Compression.NoCompression,
          meter_provider=no_metrics,
        )
        cleanup = metric_exporter.shutdown
        reader = PeriodicExportingMetricReader(
          metric_exporter,
          export_interval_millis=30000,
          export_timeout_millis=timeout * 1000,
        )
        cleanup = partial(reader.shutdown, timeout_millis=(timeout + 2) * 1000)
        metrics = MeterProvider(
          resource=resource,
          metric_readers=[reader],
          shutdown_on_exit=False,
          views=[
            # SDK defaults are too coarse for a duration measured in seconds.
            View(
              instrument_name="inkcre.operation.duration",
              aggregation=ExplicitBucketHistogramAggregation(
                boundaries=(
                  0.005,
                  0.01,
                  0.025,
                  0.05,
                  0.1,
                  0.25,
                  0.5,
                  1,
                  2.5,
                  5,
                  10,
                  30,
                  60,
                  300,
                )
              ),
            ),
            View(
              meter_name="opentelemetry-sdk",
              attribute_keys={
                "otel.component.type",
                "error.type",
                "http.response.status_code",
              },
              # An exemplar can otherwise retain attributes excluded by the View.
              exemplar_reservoir_factory=lambda _: (
                lambda _config=None, **_kwargs: SimpleFixedSizeExemplarReservoir(size=0)
              ),
            ),
          ],
        )
        cleanup = partial(metrics.shutdown, timeout_millis=(timeout + 2) * 1000)
        meter = metrics.get_meter("inkcre.metadata")
        operations = meter.create_counter("inkcre.operation.count", unit="{operation}")
        duration = meter.create_histogram("inkcre.operation.duration", unit="s")
        tokens = meter.create_counter("inkcre.ai.token.usage", unit="{token}")
        missing_usage = meter.create_counter("inkcre.ai.usage.missing", unit="{field}")
        runtime.metrics = metrics
        runtime.operations, runtime.duration = operations, duration
        runtime.tokens, runtime.missing_usage = tokens, missing_usage
    except Exception:
      if cleanup is not None:
        _shutdown(cleanup)
      _diagnostic(f"{signal} configuration or initialization failed; signal is disabled")
    else:
      runtime.connections[signal] = (endpoint, dict(headers), timeout)

  if any((runtime.traces, runtime.logs, runtime.metrics)):
    _runtime = runtime
  else:
    _diagnostic("no usable per-signal endpoint; telemetry is disabled")


def _shutdown(shutdown: Callable[[], None]) -> None:
  try:
    shutdown()
  except Exception:
    _diagnostic("signal shutdown failed; remaining telemetry may be lost")


async def close_telemetry() -> None:
  """Stop new records and concurrently drain SDK queues outside the event loop.

  SDK trace/log shutdown waits up to 30 seconds for each worker, then metrics gets
  its configured request timeout plus two seconds. These worker wait budgets are
  not transport-wide hard deadlines; freezing or termination can lose records.
  """
  global _runtime
  runtime, _runtime = _runtime, None
  if runtime is None:
    return
  shutdowns: list[Callable[[], None]] = []
  for provider in (runtime.traces, runtime.logs):
    if provider is not None:
      shutdowns.append(provider.shutdown)
  # SDK 1.45 force_flush ignores its timeout; shutdown owns the bounded queue drain.
  await asyncio.gather(*(asyncio.to_thread(_shutdown, shutdown) for shutdown in shutdowns))
  if runtime.metrics is not None:
    # Collect final native processor/exporter metrics after their workers have drained.
    await asyncio.to_thread(
      _shutdown,
      partial(
        runtime.metrics.shutdown,
        timeout_millis=(runtime.connections["metrics"][2] + 2) * 1000,
      ),
    )


def _record(action: Callable[[], object]) -> None:
  """Isolate one owned telemetry call; never wrap the caller's business block."""
  try:
    action()
  except Exception:
    _diagnostic("recording failed; optional telemetry may be lost")


@dataclass
class Operation:
  """One execution scope's result, independent of whether its Span is recorded.

  Business boundaries set outcome when they return a failure or handle cancellation.
  Span methods remain available explicitly through span; Span status is not a metric.
  Those standard SDK methods require the caller's admitted primitive metadata values;
  the facade isolates its own SDK calls, not arbitrary caller code inside the scope.
  """

  span: Span
  outcome: Literal["success", "error", "cancelled"] = "success"


@contextmanager
def operation(
  name: str,
  *,
  attributes: Mapping[str, AttributeValue] | None = None,
  context: Context | None = None,
  links: Sequence[Link] | None = None,
  kind: SpanKind = SpanKind.INTERNAL,
) -> Generator[Operation, None, None]:
  """Measure a fixed operation name; callers supply only admitted metadata values.

  Exceptions and cancellation propagate unchanged. Their messages, stack traces and
  status descriptions are never recorded by this context manager.
  """
  runtime = _runtime
  if runtime is None:
    yield Operation(INVALID_SPAN)
    return

  span = INVALID_SPAN
  if runtime.tracer is not None:
    try:
      span = runtime.tracer.start_span(
        name,
        attributes=attributes,
        context=context,
        links=links,
        kind=kind,
        record_exception=False,
        set_status_on_exception=False,
      )
    except Exception:
      _diagnostic("span creation failed; operation metrics remain available")
  observation = Operation(span)
  started = time.perf_counter()
  activation = ExitStack()
  _record(
    lambda: activation.enter_context(
      use_span(span, record_exception=False, set_status_on_exception=False)
    )
  )
  try:
    yield observation
  except BaseException as error:
    observation.outcome = (
      "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
    )
    _record(partial(span.set_attribute, "error.type", type(error).__name__))
    raise
  finally:
    # Exit the SDK context with no exception and end separately. This prevents SDK
    # context cleanup or span processors from masking the caller's original error.
    if observation.outcome != "success":
      _record(partial(span.set_status, StatusCode.ERROR))
    _record(partial(span.set_attribute, "inkcre.outcome", observation.outcome))
    labels = {"operation": name, "outcome": observation.outcome}
    if runtime.operations is not None:
      _record(partial(runtime.operations.add, 1, labels))
    if runtime.duration is not None:
      _record(partial(runtime.duration.record, time.perf_counter() - started, labels))
    _record(activation.close)
    _record(span.end)


def emit_event(name: str, attributes: Mapping[str, AttributeValue]) -> None:
  """Emit a fixed event name and admitted metadata without a Python logging bridge."""
  runtime = _runtime
  if runtime is not None and runtime.logger is not None:
    from opentelemetry._logs import SeverityNumber

    _record(
      partial(
        runtime.logger.emit,
        body=name,
        severity_number=SeverityNumber.INFO,
        attributes=attributes,
      )
    )


def record_ai_usage(
  operation: str, input_tokens: int | None, output_tokens: int | None
) -> None:
  """Count known nonnegative provider usage and count missing fields separately."""
  runtime = _runtime
  if runtime is None or runtime.tokens is None or runtime.missing_usage is None:
    return
  for kind, value in (("input", input_tokens), ("output", output_tokens)):
    labels = {"operation": operation, "token.type": kind}
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
      _record(partial(runtime.tokens.add, value, labels))
    else:
      _record(partial(runtime.missing_usage.add, 1, labels))
