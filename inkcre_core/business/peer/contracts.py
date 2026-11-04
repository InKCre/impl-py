"""Peer discovery, routing, and outbound failure contracts."""

from collections.abc import Callable
from dataclasses import dataclass
import typing

from inkcre_core.schemas.ai import JSONValue
from inkcre_core.schemas.peer import (
  CapabilityID,
  CorePeerConfig,
  PeerCapabilityAdvertisement,
  PeerModel,
  PeerProtocolID,
)


class PeerError(RuntimeError):
  """Base failure exposed by the Peer domain."""


class DuplicatePeerRegistrationError(PeerError):
  pass


class CapabilityDelegationUnavailable(PeerError):
  pass


class PeerProtocolError(PeerError):
  pass


class PeerProtocolConfigurationError(PeerProtocolError):
  """One advertised inbound cannot construct its caller-local outbound."""


class PeerRequestNotExecuted(PeerProtocolError):
  """The selected outbound proved capability execution did not begin."""


class PeerOutcomeUnknown(PeerProtocolError):
  """Dispatch may have occurred but no execution outcome is known."""


class PeerInbound(typing.Protocol):
  @property
  def capability(self) -> CapabilityID: ...

  def advertise(
    self,
    config: CorePeerConfig,
  ) -> PeerCapabilityAdvertisement | None: ...


class PeerOutbound(typing.Protocol):
  async def execute(self, payload: JSONValue) -> JSONValue: ...


PeerOutboundFactory: typing.TypeAlias = Callable[
  [PeerModel, dict[str, JSONValue]],
  PeerOutbound,
]


@dataclass(frozen=True)
class PeerOutboundRegistration:
  protocol: PeerProtocolID
  factory: PeerOutboundFactory
