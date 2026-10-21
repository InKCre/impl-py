"""Semantic state ports for shared Extensions and exact Peer selections."""

from __future__ import annotations

from collections.abc import Callable
import typing
import uuid

import pydantic

from app.persistence.extension.uow import extension_uow
from app.schemas.extension import ExtensionModel, ExtensionPeerParticipationModel

from .errors import ExtensionNotInstalledError, ExtensionStateConflictError


class InstalledExtension(pydantic.BaseModel):
  """Shared Extension identity, configuration, and producer-owned state."""

  model_config = pydantic.ConfigDict(frozen=True)

  name: str
  nickname: str | None = None
  config: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
  # Extension-produced state may contain credentials; management never serializes it.
  state: dict[str, typing.Any] = pydantic.Field(default_factory=dict, exclude=True)


class ExtensionParticipation(pydantic.BaseModel):
  """One Peer’s exact native selection and independent enabled intent."""

  model_config = pydantic.ConfigDict(frozen=True)

  name: str
  peer_id: uuid.UUID
  distribution: typing.Literal["python", "module_federation", "swift_bundled"] | None
  version: str
  enabled: bool
  config_schema: dict[str, typing.Any] | None = None


StateMutation: typing.TypeAlias = Callable[[dict[str, typing.Any]], dict[str, typing.Any]]
ConfigStateMutation: typing.TypeAlias = Callable[
  [dict[str, typing.Any], dict[str, typing.Any]],
  tuple[dict[str, typing.Any], dict[str, typing.Any]],
]


class ExtensionStore(typing.Protocol):
  async def list(self) -> tuple[InstalledExtension, ...]: ...

  async def get(self, name: str) -> InstalledExtension | None: ...

  async def ensure(self, name: str, nickname: str) -> InstalledExtension: ...

  async def uninstall(self, name: str) -> None: ...

  async def read_config(self, name: str) -> dict[str, typing.Any]: ...

  async def update_config(
    self, name: str, config: dict[str, typing.Any]
  ) -> InstalledExtension: ...

  async def read_state(self, name: str) -> dict[str, typing.Any]: ...

  async def mutate_state(
    self, name: str, transform: StateMutation
  ) -> dict[str, typing.Any]: ...

  async def mutate_config_and_state(
    self,
    name: str,
    transform: ConfigStateMutation,
  ) -> tuple[dict[str, typing.Any], dict[str, typing.Any]]: ...

  async def list_participations(
    self, peer_id: uuid.UUID
  ) -> tuple[ExtensionParticipation, ...]: ...

  async def get_participation(
    self, peer_id: uuid.UUID, name: str
  ) -> ExtensionParticipation | None: ...

  async def select_participation(
    self,
    peer_id: uuid.UUID,
    name: str,
    distribution: typing.Literal["python", "module_federation", "swift_bundled"],
    version: str,
  ) -> ExtensionParticipation: ...

  async def resolve_legacy_python_participation(
    self, peer_id: uuid.UUID, name: str, version: str
  ) -> ExtensionParticipation: ...

  async def set_participation_enabled(
    self, peer_id: uuid.UUID, name: str, enabled: bool
  ) -> ExtensionParticipation: ...

  async def remove_participation(self, peer_id: uuid.UUID, name: str) -> None: ...

  async def update_participation_config_schema(
    self,
    peer_id: uuid.UUID,
    name: str,
    distribution: str,
    version: str,
    schema: dict[str, typing.Any],
  ) -> ExtensionParticipation: ...


class ExtensionStateService:
  """Transactional adapter over shared identity and Peer-scoped state."""

  @staticmethod
  def _extension(model: ExtensionModel) -> InstalledExtension:
    return InstalledExtension(
      name=model.name,
      nickname=model.nickname,
      config=dict(model.config),
      state=dict(model.state),
    )

  @staticmethod
  def _participation(
    model: ExtensionPeerParticipationModel,
  ) -> ExtensionParticipation:
    return ExtensionParticipation(
      name=model.name,
      peer_id=model.peer_id,
      distribution=typing.cast(
        typing.Literal["python", "module_federation", "swift_bundled"] | None,
        model.distribution,
      ),
      version=model.version,
      enabled=model.enabled,
      config_schema=(
        dict(model.config_schema) if model.config_schema is not None else None
      ),
    )

  async def list(self) -> tuple[InstalledExtension, ...]:
    async with extension_uow() as repository:
      return tuple(self._extension(row) for row in await repository.list())

  async def get(self, name: str) -> InstalledExtension | None:
    async with extension_uow() as repository:
      row = await repository.get(name)
      return self._extension(row) if row is not None else None

  async def ensure(self, name: str, nickname: str) -> InstalledExtension:
    async with extension_uow() as repository:
      locked = await repository.try_install_lock(name)
      if not locked:
        raise ExtensionStateConflictError(
          f"Another Extension identity operation for {name} is in progress"
        )
      row = await repository.get(name, lock=True)
      if row is None:
        row = ExtensionModel(name=name, nickname=nickname, config={}, state={})
      else:
        row.nickname = nickname
      await repository.save(row)
      return self._extension(row)

  async def uninstall(self, name: str) -> None:
    async with extension_uow() as repository:
      row = await repository.get(name, lock=True)
      if row is None:
        raise ExtensionNotInstalledError(f"{name} is not installed")
      participations = await repository.list_participations_for_extension(name)
      if participations:
        raise ExtensionStateConflictError(
          f"Cannot uninstall {name} while Peer selections remain"
        )
      if row.state:
        raise ExtensionStateConflictError(
          f"Cannot uninstall {name} while Extension state is not empty"
        )
      await repository.delete(row)

  async def _update_json(
    self,
    name: str,
    field: typing.Literal["config"],
    value: dict[str, typing.Any],
  ) -> InstalledExtension:
    async with extension_uow() as repository:
      row = await repository.get(name, lock=True)
      if row is None:
        raise ExtensionNotInstalledError(f"{name} is not installed")
      setattr(row, field, value)
      await repository.save(row)
      return self._extension(row)

  async def update_config(
    self, name: str, config: dict[str, typing.Any]
  ) -> InstalledExtension:
    return await self._update_json(name, "config", config)

  async def read_config(self, name: str) -> dict[str, typing.Any]:
    state = await self.get(name)
    if state is None:
      raise ExtensionNotInstalledError(f"{name} is not installed")
    return dict(state.config)

  async def read_state(self, name: str) -> dict[str, typing.Any]:
    state = await self.get(name)
    if state is None:
      raise ExtensionNotInstalledError(f"{name} is not installed")
    return dict(state.state)

  async def mutate_state(
    self, name: str, transform: StateMutation
  ) -> dict[str, typing.Any]:
    async with extension_uow() as repository:
      row = await repository.get(name, lock=True)
      if row is None:
        raise ExtensionNotInstalledError(f"{name} is not installed")
      row.state = transform(dict(row.state))
      await repository.save(row)
      return dict(row.state)

  async def mutate_config_and_state(
    self,
    name: str,
    transform: ConfigStateMutation,
  ) -> tuple[dict[str, typing.Any], dict[str, typing.Any]]:
    async with extension_uow() as repository:
      row = await repository.get(name, lock=True)
      if row is None:
        raise ExtensionNotInstalledError(f"{name} is not installed")
      config, state = transform(dict(row.config), dict(row.state))
      row.config = config
      row.state = state
      await repository.save(row)
      return dict(row.config), dict(row.state)

  async def list_participations(
    self, peer_id: uuid.UUID
  ) -> tuple[ExtensionParticipation, ...]:
    async with extension_uow() as repository:
      rows = await repository.list_participations(peer_id)
      return tuple(self._participation(row) for row in rows)

  async def get_participation(
    self, peer_id: uuid.UUID, name: str
  ) -> ExtensionParticipation | None:
    async with extension_uow() as repository:
      row = await repository.get_participation(name, peer_id)
      return self._participation(row) if row is not None else None

  async def select_participation(
    self,
    peer_id: uuid.UUID,
    name: str,
    distribution: typing.Literal["python", "module_federation", "swift_bundled"],
    version: str,
  ) -> ExtensionParticipation:
    async with extension_uow() as repository:
      extension = await repository.get(name)
      if extension is None:
        raise ExtensionNotInstalledError(f"{name} is not installed")
      row = await repository.get_participation(name, peer_id, lock=True)
      if row is None:
        row = ExtensionPeerParticipationModel(
          name=name,
          peer_id=peer_id,
          distribution=distribution,
          version=version,
          enabled=False,
        )
      elif row.enabled and (row.distribution, row.version) != (distribution, version):
        raise ExtensionStateConflictError(
          f"Cannot change {name} selection while it is enabled on this Peer"
        )
      else:
        row.distribution = distribution
        row.version = version
        row.config_schema = None
      await repository.save_participation(row)
      return self._participation(row)

  async def resolve_legacy_python_participation(
    self, peer_id: uuid.UUID, name: str, version: str
  ) -> ExtensionParticipation:
    """Resolve an enabled legacy pin after its exact Python release was verified."""
    async with extension_uow() as repository:
      row = await repository.get_participation(name, peer_id, lock=True)
      if row is None or (row.distribution, row.version, row.enabled) != (
        None,
        version,
        True,
      ):
        raise ExtensionStateConflictError(
          f"{name} legacy selection changed before its Python distribution was resolved"
        )
      row.distribution = "python"
      row.config_schema = None
      await repository.save_participation(row)
      return self._participation(row)

  async def set_participation_enabled(
    self, peer_id: uuid.UUID, name: str, enabled: bool
  ) -> ExtensionParticipation:
    async with extension_uow() as repository:
      row = await repository.get_participation(name, peer_id, lock=True)
      if row is None:
        raise ExtensionNotInstalledError(f"{name} has no selection on this Peer")
      if row.distribution is None:
        raise ExtensionStateConflictError(
          f"{name} has unresolved legacy distribution; select its exact distribution first"
        )
      row.enabled = enabled
      await repository.save_participation(row)
      return self._participation(row)

  async def remove_participation(self, peer_id: uuid.UUID, name: str) -> None:
    async with extension_uow() as repository:
      row = await repository.get_participation(name, peer_id, lock=True)
      if row is None:
        raise ExtensionNotInstalledError(f"{name} has no selection on this Peer")
      if row.enabled:
        raise ExtensionStateConflictError(
          f"Disable {name} on this Peer before removing its selection"
        )
      await repository.delete_participation(row)

  async def update_participation_config_schema(
    self,
    peer_id: uuid.UUID,
    name: str,
    distribution: str,
    version: str,
    schema: dict[str, typing.Any],
  ) -> ExtensionParticipation:
    async with extension_uow() as repository:
      row = await repository.get_participation(name, peer_id, lock=True)
      if row is None or (row.distribution, row.version) != (distribution, version):
        raise ExtensionStateConflictError(
          f"{name} selection changed while its configuration schema was loading"
        )
      row.config_schema = schema
      await repository.save_participation(row)
      return self._participation(row)
