"""Persisted Sink catalog, instances, and Peer-local lifecycle."""

from __future__ import annotations

import asyncio
import logging
import typing

import fastapi

from inkcre_core.persistence.sink.uow import sink_uow
from inkcre_core.schemas.peer import PeerRef
from inkcre_core.schemas.sink import SinkID, SinkModel, SinkTypeID, SinkTypeModel

from .errors import (
  DuplicateSinkRegistrationError,
  SinkNotFoundError,
  SinkStateConflictError,
  UnknownSinkTypeError,
)

if typing.TYPE_CHECKING:
  from .base import SinkBase


logger = logging.getLogger(__name__)


class SinkManager:
  """Own Sink registration, persistence, and local instance realization."""

  _SINK_CLASSES: dict[SinkTypeID, type["SinkBase"]] = {}
  _running: dict[SinkID, "SinkBase"] = {}
  _locks: dict[SinkID, asyncio.Lock] = {}
  _app: fastapi.FastAPI | None = None

  @classmethod
  def register_sink_type(cls, sink_cls: type["SinkBase"]) -> None:
    existing = cls._SINK_CLASSES.get(sink_cls.__sinktype__)
    if existing is sink_cls:
      return
    if existing is not None:
      raise DuplicateSinkRegistrationError(
        f"Sink type {sink_cls.__sinktype__!r} is already registered"
      )
    cls._SINK_CLASSES[sink_cls.__sinktype__] = sink_cls

  @classmethod
  async def sync_sink_types(cls) -> None:
    rows = [
      dict(
        id=sink_cls.__sinktype__,
        description=sink_cls.__doc__ or "No description.",
        config_schema=sink_cls.__configschema__,
      )
      for sink_cls in cls._SINK_CLASSES.values()
    ]
    async with sink_uow() as repository:
      await repository.sync_types(rows)

  @classmethod
  async def list_types(cls) -> tuple[SinkTypeModel, ...]:
    async with sink_uow() as repository:
      return await repository.list_types()

  @classmethod
  async def get_type(cls, sink_type: SinkTypeID) -> SinkTypeModel | None:
    async with sink_uow() as repository:
      return await repository.get_type(sink_type)

  @classmethod
  async def list(cls) -> tuple[SinkModel, ...]:
    async with sink_uow() as repository:
      return await repository.list()

  @classmethod
  async def get(cls, sink_id: SinkID) -> SinkModel:
    async with sink_uow() as repository:
      sink = await repository.get(sink_id)
    if sink is None:
      raise SinkNotFoundError(f"Sink {sink_id} does not exist")
    return sink

  @classmethod
  async def create(
    cls,
    sink_type: SinkTypeID,
    *,
    nickname: str | None = None,
    config: dict[str, typing.Any] | None = None,
  ) -> SinkModel:
    sink_cls = cls._require_type(sink_type)
    normalized = sink_cls.__configcls__.model_validate(config or {}).model_dump(mode="json")
    async with sink_uow() as repository:
      sink = SinkModel(type=sink_type, nickname=nickname, config=normalized)
      await repository.save(sink)
      return sink

  @classmethod
  async def update_config(
    cls,
    sink_id: SinkID,
    value: dict[str, typing.Any],
  ) -> SinkModel:
    async with cls._lock(sink_id):
      current = await cls.get(sink_id)
      sink_cls = cls._require_type(current.type)
      validated = sink_cls.__configcls__.model_validate(value)
      normalized = validated.model_dump(mode="json")
      async with sink_uow() as repository:
        sink = await repository.get(sink_id)
        if sink is None:
          raise SinkNotFoundError(f"Sink {sink_id} does not exist")
        sink.config = normalized
        await repository.save(sink)
      running = cls._running.get(sink_id)
      if running is not None:
        running.update_config(validated)
      return sink

  @classmethod
  async def delete(cls, sink_id: SinkID) -> None:
    async with cls._lock(sink_id):
      async with sink_uow() as repository:
        sink = await repository.get(sink_id)
        if sink is None:
          raise SinkNotFoundError(f"Sink {sink_id} does not exist")
        if sink.enabled or sink_id in cls._running:
          raise SinkStateConflictError("Disable the Sink before deleting it")
        await repository.delete(sink)

  @classmethod
  async def enable(cls, sink_id: SinkID, peer: PeerRef) -> SinkModel:
    async with cls._lock(sink_id):
      sink = await cls._set_peer_enabled(sink_id, peer, True)
      if sink_id in cls._running:
        return sink
      if cls._app is None:
        raise SinkStateConflictError("Sink runtime has not started")
      sink_cls = cls._require_type(sink.type)
      instance = sink_cls(sink)
      await instance.on_start(cls._app)
      cls._running[sink_id] = instance
      return sink

  @classmethod
  async def disable(cls, sink_id: SinkID, peer: PeerRef) -> SinkModel:
    async with cls._lock(sink_id):
      sink = await cls._set_peer_enabled(sink_id, peer, False)
      await cls._close_running(sink_id)
      return sink

  @classmethod
  async def startup(cls, app: fastapi.FastAPI, peer: PeerRef) -> None:
    cls._app = app
    await cls.sync_sink_types()
    for sink in await cls.list():
      if sink.id is None or peer not in sink.enabled:
        continue
      try:
        await cls.enable(sink.id, peer)
      except Exception:
        logger.exception("Sink failed to start", extra={"sink": sink.id, "type": sink.type})

  @classmethod
  async def shutdown(cls) -> None:
    for sink_id in tuple(cls._running)[::-1]:
      async with cls._lock(sink_id):
        try:
          await cls._close_running(sink_id)
        except Exception:
          logger.exception("Sink failed to close", extra={"sink": sink_id})
    cls._app = None

  @classmethod
  def get_running(cls, sink_id: SinkID) -> "SinkBase" | None:
    """Return this process's active instance without changing durable intent."""
    return cls._running.get(sink_id)

  @classmethod
  async def _close_running(cls, sink_id: SinkID) -> None:
    running = cls._running.get(sink_id)
    if running is None:
      return
    await running.on_close()
    cls._running.pop(sink_id, None)

  @classmethod
  def _lock(cls, sink_id: SinkID) -> asyncio.Lock:
    return cls._locks.setdefault(sink_id, asyncio.Lock())

  @classmethod
  def _require_type(cls, sink_type: SinkTypeID) -> type["SinkBase"]:
    sink_cls = cls._SINK_CLASSES.get(sink_type)
    if sink_cls is None:
      raise UnknownSinkTypeError(f"Sink type {sink_type!r} is not registered")
    return sink_cls

  @classmethod
  async def _set_peer_enabled(
    cls,
    sink_id: SinkID,
    peer: PeerRef,
    enabled: bool,
  ) -> SinkModel:
    async with sink_uow() as repository:
      sink = await repository.get(sink_id, lock=True)
      if sink is None:
        raise SinkNotFoundError(f"Sink {sink_id} does not exist")
      peers = list(sink.enabled)
      if enabled and peer not in peers:
        peers.append(peer)
      elif not enabled and peer in peers:
        peers.remove(peer)
      sink.enabled = peers
      await repository.save(sink)
      return sink
