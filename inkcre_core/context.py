"""Explicit database and execution scope for the SDK's domain operations."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4
import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
  from .libs.obsrv.runtime import ObservationRuntime
  from .libs.obsrv.telemetry import _Runtime
  from .business.job import _Execution
  from .business.peer.contracts import PeerInbound, PeerOutboundFactory
  from .business.source.main import SourceBase


@dataclass(frozen=True)
class CoreOptions:
  """Runtime values supplied by the consumer, independent of dotenv or HTTP servers."""

  peer_id: UUID = field(default_factory=uuid4)
  peer_name: str = "InKCre Python"
  application_version: str = "development"
  jwt_secret: str = ""
  jwt_default_lifetime_seconds: int = 60
  peer_http_timeout_seconds: float = 30
  agent_debug: bool = False


_CURRENT: ContextVar[Core] = ContextVar("inkcre_core")


def current_core() -> Core:
  """Require an explicit binding; there is no default deployment or global engine."""
  core = _CURRENT.get(None)
  if core is None:
    raise RuntimeError("Use 'with core.bind():' around InKCre operations")
  if core._closed:
    raise RuntimeError("This InKCre Core has been closed")
  return core


class Core:
  """Own SDK resources, or borrow an engine without taking its disposal authority.

  Binding is task-local and inherited by child tasks. Each operation still creates
  its own short-lived Unit of Work; sessions are never stored in the context.
  """

  def __init__(
    self,
    database: str | AsyncEngine,
    *,
    options: CoreOptions | None = None,
    pool_pre_ping: bool = False,
  ) -> None:
    self.options = options or CoreOptions()
    self._owns_engine = isinstance(database, str)
    self.engine = (
      create_async_engine(database, pool_pre_ping=pool_pre_ping)
      if isinstance(database, str)
      else database
    )
    self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
    self.sources: dict[int, SourceBase] = {}
    self.inbounds: dict[str, PeerInbound] = {}
    self.outbounds: dict[str, PeerOutboundFactory] = {}
    self.active_jobs: dict[int, _Execution] = {}
    self.pending_jobs: dict[int, asyncio.Task[bool]] = {}
    self.accepting_jobs = False
    self.observability: ObservationRuntime | None = None
    self.telemetry: _Runtime | None = None
    self._closed = False

  @contextmanager
  def bind(self) -> Generator[Core, None, None]:
    """Bind this instance for an operation; nested bindings restore their caller."""
    if self._closed:
      raise RuntimeError("This InKCre Core has been closed")
    token = _CURRENT.set(self)
    try:
      yield self
    finally:
      _CURRENT.reset(token)

  async def aclose(self) -> None:
    """Drain this instance's workers and dispose only an engine it created."""
    if self._closed:
      return
    with self.bind():
      if self.pending_jobs or self.active_jobs:
        from .business.job import JobManager

        await JobManager.shutdown()
      self.accepting_jobs = False
      self.sources.clear()
      self.inbounds.clear()
      self.outbounds.clear()
      from .libs.obsrv.telemetry import close_telemetry

      await close_telemetry()
      if self.observability is not None:
        await self.observability.aclose()
        self.observability = None
      if self._owns_engine:
        await self.engine.dispose()
      self._closed = True
