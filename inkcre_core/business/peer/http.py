"""core.peer.protocol.http.v1 inbound advertisement and one-shot outbound."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import typing

import httpx
import pydantic

from inkcre_core.auth import create_peer_jwt
from inkcre_core.schemas.ai import JSONValue
from inkcre_core.schemas.peer import (
  PEER_EXECUTION_HEADER,
  PEER_HTTP_PROTOCOL,
  PEER_NOT_EXECUTED,
  CapabilityID,
  CorePeerConfig,
  PeerCapabilityAdvertisement,
  PeerHTTPInboundParameters,
  PeerInboundInterface,
  PeerModel,
  PeerProtocolRequest,
  PeerProtocolResponse,
)
from inkcre_core.context import current_core
from inkcre_core.libs.obsrv.propagation import inject_context
from inkcre_core.libs.obsrv.telemetry import operation
from opentelemetry.trace import SpanKind

from .contracts import (
  PeerOutcomeUnknown,
  PeerProtocolConfigurationError,
  PeerProtocolError,
  PeerRequestNotExecuted,
)


_RESERVED_REQUEST_HEADERS = frozenset(
  {
    "authorization",
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
  }
)


@dataclass(frozen=True)
class PeerHTTPInbound:
  """One fixed Business route advertised through Peer HTTP v1."""

  capability: CapabilityID
  method: str
  path: str

  def __post_init__(self) -> None:
    if not self.path.startswith("/") or "?" in self.path or "#" in self.path:
      raise ValueError("Peer HTTP inbound path must be one absolute path")

  def advertise(
    self,
    config: CorePeerConfig,
  ) -> PeerCapabilityAdvertisement | None:
    if config.http_public_base_url is None:
      return None
    parameters = PeerHTTPInboundParameters(
      method=self.method,
      url=f"{config.http_public_base_url}{self.path}",
    )
    return PeerCapabilityAdvertisement(
      id=self.capability,
      inbound=PeerInboundInterface(
        protocol=PEER_HTTP_PROTOCOL,
        parameters=typing.cast(
          dict[str, JSONValue],
          parameters.model_dump(mode="json"),
        ),
      ),
    )


class PeerHTTPOutbound:
  """One-shot authenticated JSON HTTP execution for one advertised inbound."""

  def __init__(
    self,
    peer: PeerModel,
    parameters: dict[str, JSONValue],
  ) -> None:
    self.peer = peer
    try:
      self.parameters = PeerHTTPInboundParameters.model_validate(parameters)
    except pydantic.ValidationError as error:
      raise PeerProtocolConfigurationError(
        f"Peer {peer.id} published invalid HTTP inbound parameters"
      ) from error

  async def execute(self, payload: JSONValue) -> JSONValue:
    try:
      request = PeerProtocolRequest.model_validate(payload)
    except pydantic.ValidationError as error:
      raise PeerProtocolError("Invalid Peer HTTP request envelope") from error

    reserved = _RESERVED_REQUEST_HEADERS.intersection(request.headers)
    if reserved:
      rendered = ", ".join(sorted(reserved))
      raise PeerProtocolError(f"Peer HTTP payload attempted reserved headers: {rendered}")

    query = [(name, value) for name, values in request.query.items() for value in values]
    headers = [
      (name, value) for name, values in request.headers.items() for value in values
    ]
    options = current_core().options
    token = create_peer_jwt(
      options.jwt_secret, lifetime_seconds=options.jwt_default_lifetime_seconds
    )
    headers.append(("authorization", f"Bearer {token}"))
    kwargs: dict[str, typing.Any] = {
      "method": self.parameters.method,
      "url": self.parameters.url,
      "params": query,
      "headers": headers,
    }
    if "body" in request.model_fields_set:
      kwargs["json"] = request.body

    with operation(
      "peer.http",
      attributes={"inkcre.peer.target_id": str(self.peer.id)},
      kind=SpanKind.CLIENT,
    ) as observation:
      span = observation.span
      propagated = inject_context()
      if propagated:
        # The transport owns Trace Context; business payload headers cannot duplicate it.
        headers[:] = [
          (key, value)
          for key, value in headers
          if key.lower() not in {"traceparent", "tracestate"}
        ]
        headers.extend(propagated.items())
      try:
        async with httpx.AsyncClient(
          timeout=current_core().options.peer_http_timeout_seconds,
        ) as client:
          response = await client.request(**kwargs)
      except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
        span.set_attribute("inkcre.peer.outcome", "not_executed")
        raise PeerRequestNotExecuted(
          f"Could not dispatch to Peer {self.peer.id}"
        ) from error
      except httpx.RequestError as error:
        span.set_attribute("inkcre.peer.outcome", "unknown")
        raise PeerOutcomeUnknown(
          f"Peer {self.peer.id} dispatch outcome is unknown"
        ) from error

      if (
        response.headers.get(PEER_EXECUTION_HEADER, "").strip().lower() == PEER_NOT_EXECUTED
      ):
        span.set_attribute("inkcre.peer.outcome", "not_executed")
        raise PeerRequestNotExecuted(
          f"Peer {self.peer.id} reported that execution did not begin"
        )

      span.set_attribute("inkcre.peer.outcome", "responded")
      span.set_attribute("http.response.status_code", response.status_code)
      if response.status_code >= 500:
        observation.outcome = "error"

      grouped_headers: defaultdict[str, list[str]] = defaultdict(list)
      for name, value in response.headers.multi_items():
        if name.lower() != PEER_EXECUTION_HEADER.lower():
          grouped_headers[name.lower()].append(value)

      response_fields: dict[str, typing.Any] = {
        "status": response.status_code,
        "headers": grouped_headers,
      }
      if response.content:
        try:
          response_fields["body"] = response.json()
        except json.JSONDecodeError as error:
          raise PeerProtocolError(
            f"Peer {self.peer.id} returned a non-JSON HTTP body"
          ) from error
      normalized = PeerProtocolResponse.model_validate(response_fields)
      return typing.cast(
        JSONValue,
        normalized.model_dump(mode="json", exclude_unset=True),
      )
