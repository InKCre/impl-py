"""Authenticated, bounded OTLP forwarding for browser Peers without ingest secrets."""

import asyncio
from typing import Literal

import fastapi
from google.protobuf.message import DecodeError  # type: ignore[untyped-import]
import httpx
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
  ExportLogsServiceRequest,
  ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
  ExportMetricsServiceRequest,
  ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
  ExportTraceServiceRequest,
  ExportTraceServiceResponse,
)

from inkcre_core.libs.obsrv.telemetry import get_export_connection


ROUTER = fastapi.APIRouter(prefix="/telemetry", tags=["telemetry"])
_REQUESTS = {
  "traces": ExportTraceServiceRequest,
  "logs": ExportLogsServiceRequest,
  "metrics": ExportMetricsServiceRequest,
}
_RESPONSES = {
  "traces": ExportTraceServiceResponse,
  "logs": ExportLogsServiceResponse,
  "metrics": ExportMetricsServiceResponse,
}
_MAX_BYTES = 256 * 1024
_MAX_IN_FLIGHT = 4
_in_flight = 0


@ROUTER.post("/v1/{signal}", response_class=fastapi.Response)
async def export_telemetry(
  signal: Literal["traces", "logs", "metrics"], request: fastapi.Request
) -> fastapi.Response:
  """Forward standard OTLP using this Peer's enabled private destination.

  The core router requires the existing Peer JWT. The caller cannot choose an
  upstream URL or forward headers. No application payload or durable queue is created.
  """
  global _in_flight
  connection = get_export_connection(signal)
  if connection is None:
    raise fastapi.HTTPException(503, "Telemetry destination is not enabled")
  if _in_flight >= _MAX_IN_FLIGHT:
    raise fastapi.HTTPException(429, "Telemetry forwarding capacity reached")
  media_type = request.headers.get("content-type", "").partition(";")[0].strip()
  if media_type != "application/x-protobuf":
    raise fastapi.HTTPException(415, "OTLP protobuf is required")
  if request.headers.get("content-encoding", "identity") != "identity":
    raise fastapi.HTTPException(415, "Compressed telemetry requests are not supported")

  endpoint, headers, timeout = connection
  # No await between admission and increment: the ASGI event loop owns this bound.
  _in_flight += 1
  try:
    async with asyncio.timeout(timeout + 2):
      body = bytearray()
      async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_BYTES:
          raise fastapi.HTTPException(413, "Telemetry request exceeds 256 KiB")
        body.extend(chunk)
      message = _REQUESTS[signal]()
      try:
        message.ParseFromString(bytes(body))
      except DecodeError as error:
        raise fastapi.HTTPException(400, "Invalid OTLP request") from error

      headers = {
        **{
          key: value
          for key, value in headers.items()
          if key.lower() not in {"content-type", "accept"}
        },
        "Content-Type": "application/x-protobuf",
        "Accept": "application/x-protobuf",
      }
      async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.post(
          endpoint, headers=headers, content=message.SerializeToString()
        )
      if response.status_code not in (200, 204):
        # An upstream error body could echo server-side authentication or infrastructure.
        raise fastapi.HTTPException(502, "Telemetry destination did not accept the batch")
      exported = _RESPONSES[signal]()
      try:
        exported.ParseFromString(response.content)
      except DecodeError as error:
        raise fastapi.HTTPException(
          502, "Invalid telemetry destination response"
        ) from error
      return fastapi.Response(exported.SerializeToString(), media_type=media_type)
  except (TimeoutError, httpx.HTTPError) as error:
    raise fastapi.HTTPException(502, "Telemetry forwarding did not complete") from error
  finally:
    _in_flight -= 1
