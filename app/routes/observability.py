"""Authenticated, bounded observability capability; no caller-selected destination."""

import asyncio

import fastapi
from pydantic import ValidationError

from inkcre_core.business.peer import PeerHTTPInbound
from inkcre_core.libs.obsrv.backends import ObservabilityUnavailable
from inkcre_core.libs.obsrv.records import ObservationRequest
from inkcre_core.libs.obsrv.runtime import current_observability

ROUTER = fastapi.APIRouter(tags=["observability"])
PEER_INBOUND = PeerHTTPInbound(
  capability="core.observability.v1", method="POST", path="/observability"
)
_MAX_BYTES = 256 * 1024


@ROUTER.post("/observability")
async def observability(request: fastapi.Request):
  runtime = current_observability()
  if runtime is None:
    raise fastapi.HTTPException(503, "Observability is not initialized")
  if runtime.requests_in_flight >= 4:
    raise fastapi.HTTPException(429, "Observability request capacity reached")
  runtime.requests_in_flight += 1
  try:
    async with asyncio.timeout(15):
      payload = bytearray()
      async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > _MAX_BYTES:
          raise fastapi.HTTPException(413, "Observability request exceeds 256 KiB")
      try:
        body = ObservationRequest.model_validate_json(payload)
      except ValidationError as error:
        # Validation details can contain the original body. Do not reflect log contents.
        raise fastapi.HTTPException(422, "Invalid observability request") from error
      if body.expected_backend is not None and body.expected_backend != runtime.selected:
        raise fastapi.HTTPException(409, "Observability backend selection changed")
      if body.action == "status":
        return runtime.status()
      if body.action == "query":
        assert body.query is not None
        try:
          return await runtime.query(body.query)
        except ObservabilityUnavailable as error:
          raise fastapi.HTTPException(
            503, "Selected observability backend is unavailable"
          ) from error
      assert body.records is not None
      if not runtime.status().recording:
        raise fastapi.HTTPException(503, "Selected observability backend is not recording")
      accepted = sum(runtime.emit(record) for record in body.records)
      return {"accepted": accepted, "dropped": len(body.records) - accepted}
  except TimeoutError as error:
    raise fastapi.HTTPException(408, "Observability request timed out") from error
  finally:
    runtime.requests_in_flight -= 1
