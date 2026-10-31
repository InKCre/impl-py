"""One Core Extension Host over Registry-native Python Distributions."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import importlib.metadata
from pathlib import Path
import tempfile
import typing

import fastapi
from inkcre_extension_runtime_core_py import EmptyConfig
from inkcre_extension_runtime_core_py import ExtensionBase as RuntimeExtensionBase
import pydantic
import jsonschema  # pyrefly: ignore[untyped-import]
from packaging.utils import canonicalize_name
import sqlmodel

from app.business.peer import PeerManager
from app.schemas.extension import (
  DisableExtensionCommand,
  EnableExtensionCommand,
  ExtensionPeerStatus,
  ExtensionPeerStatusRow,
  ExtensionManagementCommand,
  ExtensionStatusCommand,
  PatchExtensionConfigCommand,
  SelectExtensionCommand,
  UnselectExtensionCommand,
)
from app.schemas.peer import PeerProtocolRequest, PeerProtocolResponse, PeerRef
from app.settings import settings
from app.version import CORE_VERSION
from app.runtime import request_process_restart
from libs.obsrv.main import get_logger

from .distribution import (
  AcquiredDistribution,
  DistributionConsumer,
  DistributionModules,
  PipDistributionConsumer,
)
from .config import resolve_extension_registry_origin
from .errors import (
  ExtensionCompatibilityError,
  ExtensionEnvironmentApplicationRequiredError,
  ExtensionHostError,
  ExtensionNotInstalledError,
  ExtensionRestartRequiredError,
  ExtensionRuntimeError,
  ExtensionStateConflictError,
)
from .release import (
  ExtensionReleaseDescriptor,
  PythonReleaseDescriptor,
  RegistryReleaseClient,
  ReleaseState,
  ReleaseResolver,
  require_python_association,
  validate_coordinate,
)
from .runtime import (
  PublicHTTPRoute,
  ExtensionRuntimeClaim,
  ExtensionRuntimeClaimConflictError,
)
from .state import (
  ExtensionParticipation,
  ExtensionStore,
  InstalledExtension,
  ExtensionStateService,
)


LOGGER = get_logger().getChild(__name__)
EXTENSION_MANAGEMENT_CAPABILITY = "core.extension.management.v2"


async def _finish_before_cancelling[T](
  work: typing.Coroutine[typing.Any, typing.Any, T],
) -> T:
  """Keep resources alive until work that cannot be abandoned has finished."""
  task = asyncio.create_task(work)
  cancellation: asyncio.CancelledError | None = None
  while not task.done():
    try:
      await asyncio.shield(task)
    except asyncio.CancelledError as error:
      # Another cancellation must not release the caller's lock or temporary
      # directory while its worker still owns them.
      cancellation = cancellation or error
    except Exception:
      break
  try:
    result = task.result()
  except Exception as error:
    if cancellation is None:
      raise
    LOGGER.exception("Extension operation failed while its caller was cancelling")
    raise cancellation from error
  if cancellation is not None:
    raise cancellation
  return result


class ExtensionDelegationError(RuntimeError):
  """The selected Peer did not return the Extension management contract."""


class ExtensionBase(RuntimeExtensionBase, ext_id="_facade"):
  """Core import facade retaining the established one-parameter type spelling."""

  config: typing.Any

  @classmethod
  def __class_getitem__(cls, _item: typing.Any) -> type[ExtensionBase]:
    return cls

  @classmethod
  async def on_start_async(cls, app: typing.Any) -> None:
    await super().on_start_async(app)
    cls.config = cls.get_config()

  @classmethod
  async def update_config_async(cls, value: typing.Any) -> typing.Any:
    config = await super().update_config_async(value)
    cls.config = config
    return config

  @classmethod
  async def mutate_config_and_state_async(
    cls, transform: typing.Any
  ) -> tuple[typing.Any, typing.Any]:
    config, state = await super().mutate_config_and_state_async(transform)
    cls.config = config
    return config, state

  @classmethod
  def validate_config(cls, config: dict[str, typing.Any]) -> typing.Any:
    return cls.__configcls__.model_validate(config)


@dataclass
class _ActiveExtensionModel:
  """Bind the Core store to the Runtime's rich active-record interface."""

  name: str
  config: dict[str, typing.Any]
  store: ExtensionStore
  peer_id: typing.Any
  distribution: str
  version: str

  async def _refresh_async(self) -> _ActiveExtensionModel:
    self.config = await self.store.read_config(self.name)
    return self

  async def update_config_async(
    self, config: dict[str, typing.Any]
  ) -> _ActiveExtensionModel:
    persisted = await self.store.update_config(self.name, config)
    self.config = dict(persisted.config)
    return self

  async def update_config_schema_async(
    self, schema: dict[str, typing.Any]
  ) -> _ActiveExtensionModel:
    await self.store.update_participation_config_schema(
      self.peer_id, self.name, self.distribution, self.version, schema
    )
    return self

  async def read_state_async(self) -> dict[str, typing.Any]:
    return await self.store.read_state(self.name)

  async def mutate_state_async(
    self,
    transform: typing.Callable[[dict[str, typing.Any]], dict[str, typing.Any]],
  ) -> dict[str, typing.Any]:
    return await self.store.mutate_state(self.name, transform)

  async def mutate_config_and_state_async(
    self,
    transform: typing.Callable[
      [dict[str, typing.Any], dict[str, typing.Any]],
      tuple[dict[str, typing.Any], dict[str, typing.Any]],
    ],
  ) -> tuple[dict[str, typing.Any], dict[str, typing.Any]]:
    result = await self.store.mutate_config_and_state(self.name, transform)
    self.config = dict(result[0])
    return result


@dataclass
class RunningExtension:
  name: str
  version: str
  association: PythonReleaseDescriptor
  acquired: AcquiredDistribution
  extension_class: type[ExtensionBase]
  claim: ExtensionRuntimeClaim


class ExtensionHost:
  """Core’s Python-only runtime over shared identities and Peer selections."""

  def __init__(
    self,
    *,
    store: ExtensionStore | None = None,
    release_client: ReleaseResolver | None = None,
    distribution_consumer: DistributionConsumer | None = None,
    registry_origin_resolver: typing.Callable[[], typing.Awaitable[str]] | None = None,
  ) -> None:
    self.store = store or ExtensionStateService()
    self.release_client = release_client
    self.distribution_consumer = distribution_consumer
    self.registry_origin_resolver = (
      registry_origin_resolver or resolve_extension_registry_origin
    )
    self.running: dict[str, RunningExtension] = {}
    self.fastapi_app: fastapi.FastAPI | None = None
    self._loaded_versions: dict[str, str] = {}
    self._runtime_lock = asyncio.Lock()
    self._operations: dict[str, tuple[str | None, str | None]] = {}
    self._environment_application_pending = False
    self.pause_peer_execution: typing.Callable[[], typing.Awaitable[None]] | None = None

  async def list(self) -> tuple[InstalledExtension, ...]:
    return await self.store.list()

  async def get(self, name: str) -> InstalledExtension:
    validate_coordinate(name)
    state = await self.store.get(name)
    if state is None:
      raise ExtensionNotInstalledError(f"{name} is not installed")
    return state

  async def status(
    self,
    peer_id: typing.Any,
    extension: str | None = None,
  ) -> ExtensionPeerStatus:
    if extension is not None:
      validate_coordinate(extension)
    names = (extension,) if extension is not None else None
    shared = await self.store.list()
    if names is not None:
      shared = tuple(row for row in shared if row.name == names[0])
    participations = {
      row.name: row for row in await self.store.list_participations(peer_id)
    }
    supported = ("python",)
    rows = []
    for state in shared:
      selected = participations.get(state.name)
      running = self.running.get(state.name)
      operation, error = self._operations.get(state.name, (None, None))
      restart_required = operation in {"applying", "restarting"} or (
        operation == "failed" and self._environment_application_pending
      )
      if selected is not None and selected.distribution == "python":
        restart_required = restart_required or (
          self._loaded_versions.get(state.name) not in (None, selected.version)
        )
        if not restart_required:
          try:
            installed = await asyncio.to_thread(
              AcquiredDistribution.discover,
              state.name,
              selected.version,
              CORE_VERSION,
            )
            restart_required = installed is None
          except Exception as failure:
            error = str(failure)
      rows.append(
        ExtensionPeerStatusRow(
          name=state.name,
          peer_id=peer_id,
          distribution=selected.distribution if selected else None,
          version=selected.version if selected else None,
          enabled=selected.enabled if selected else False,
          config_schema=selected.config_schema if selected else None,
          supported_distributions=supported,
          loaded_version=(
            running.version if running else self._loaded_versions.get(state.name)
          ),
          running=running is not None,
          restart_required=restart_required,
          operation=operation,
          error=error,
        )
      )
    return ExtensionPeerStatus(
      peer_id=peer_id,
      supported_distributions=supported,
      extensions=tuple(rows),
    )

  async def manage(
    self,
    command: ExtensionManagementCommand,
    *,
    route_to_peer: PeerRef,
  ) -> ExtensionPeerStatus:
    if route_to_peer == PeerManager.get_current_peer_ref():
      return await self.manage_local(command)
    request = PeerProtocolRequest(body=command.model_dump(mode="json"))
    result = await PeerManager.delegate(
      EXTENSION_MANAGEMENT_CAPABILITY,
      typing.cast(typing.Any, request.model_dump(mode="json", exclude_unset=True)),
      route_to_peer=route_to_peer,
    )
    try:
      response = PeerProtocolResponse.model_validate(result)
      if response.status != 200 or "body" not in response.model_fields_set:
        raise ExtensionDelegationError(
          f"Extension management Peer returned HTTP {response.status}"
        )
      return ExtensionPeerStatus.model_validate(response.body)
    except pydantic.ValidationError as error:
      raise ExtensionDelegationError(
        "Extension management Peer returned an invalid response"
      ) from error

  async def manage_local(self, command: ExtensionManagementCommand) -> ExtensionPeerStatus:
    peer_id = PeerManager.get_current_peer_ref()
    if isinstance(command, ExtensionStatusCommand):
      return await self.status(peer_id, command.extension)
    if isinstance(command, SelectExtensionCommand):
      await self.select(
        command.extension, command.distribution, command.version, peer_id=peer_id
      )
    elif isinstance(command, EnableExtensionCommand):
      await self.enable(command.extension, peer_id=peer_id)
    elif isinstance(command, DisableExtensionCommand):
      await self.disable(command.extension, peer_id=peer_id)
    elif isinstance(command, UnselectExtensionCommand):
      await self.unselect(command.extension, peer_id=peer_id)
    elif isinstance(command, PatchExtensionConfigCommand):
      await self.patch_config(command.extension, command.patch, peer_id=peer_id)
    else:
      typing.assert_never(command)
    return await self.status(peer_id, command.extension)

  def _resolve(
    self,
    name: str,
    version: str,
    *,
    allow_yanked: bool,
    release_client: ReleaseResolver,
  ):
    release = release_client.get(name, version)
    if release.state is ReleaseState.yanked and allow_yanked:
      LOGGER.warning("Using yanked exact Release %s@%s", name, version)
    elif release.state is not ReleaseState.published:
      raise ExtensionCompatibilityError(
        f"{name}@{version} is not available for this operation"
      )
    return release

  async def select(
    self,
    name: str,
    distribution: str,
    version: str,
    *,
    peer_id: typing.Any,
  ) -> ExtensionParticipation:
    async with self._runtime_lock:
      return await self._select_locked(name, distribution, version, peer_id=peer_id)

  async def _select_locked(
    self,
    name: str,
    distribution: str,
    version: str,
    *,
    peer_id: typing.Any,
  ) -> ExtensionParticipation:
    if self._environment_application_pending:
      raise ExtensionStateConflictError(
        "The Peer is paused while its Python environment is being applied"
      )
    validate_coordinate(name, version)
    if distribution != "python":
      raise ExtensionCompatibilityError(
        "This Core Peer supports only Python Extension distributions"
      )
    await self._require_selectable(name, version, peer_id=peer_id)
    release_client, consumer = await self._operation_consumers()
    release = await asyncio.to_thread(
      self._resolve,
      name,
      version,
      allow_yanked=False,
      release_client=release_client,
    )
    association = require_python_association(release)
    installed = await asyncio.to_thread(
      AcquiredDistribution.discover, name, version, CORE_VERSION
    )
    install_required = installed is None
    loaded_version = self._loaded_versions.get(name)
    restart_required = loaded_version not in (None, version)
    if installed is not None:
      self._validate_installed_association(installed, association)
    if install_required:
      if self.pause_peer_execution is None:
        raise ExtensionEnvironmentApplicationRequiredError(
          f"{name}@{version} must be applied while the Peer is paused; "
          "the process restart lifecycle is unavailable"
        )
      self._operations[name] = ("downloading", None)
      with tempfile.TemporaryDirectory(prefix="inkcre-extension-wheel-") as directory:
        try:
          extension_wheel = await _finish_before_cancelling(
            asyncio.to_thread(
              consumer.download, release, association, CORE_VERSION, Path(directory)
            )
          )
        except asyncio.CancelledError:
          self._operations[name] = (None, "Extension download was cancelled")
          raise
        except Exception as error:
          self._operations[name] = ("failed", str(error))
          raise ExtensionEnvironmentApplicationRequiredError(
            f"Preparing {name}@{version} failed: {error}"
          ) from error
        return await _finish_before_cancelling(
          self._apply_selection(release, association, consumer, extension_wheel, peer_id)
        )
    await self.store.ensure(name, release.nickname)
    selected = await self.store.select_participation(peer_id, name, "python", version)
    if restart_required:
      self._environment_application_pending = True
      self._operations[name] = ("restarting", None)
      request_process_restart()
    else:
      self._operations[name] = (None, None)
    return selected

  async def _require_selectable(
    self, name: str, version: str, *, peer_id: typing.Any
  ) -> None:
    selection = await self.store.get_participation(peer_id, name)
    if (
      selection is not None
      and selection.enabled
      and (
        selection.distribution,
        selection.version,
      )
      != ("python", version)
    ):
      raise ExtensionStateConflictError(
        f"Cannot change {name} selection while it is enabled on this Peer"
      )

  async def _apply_selection(
    self,
    release: ExtensionReleaseDescriptor,
    association: PythonReleaseDescriptor,
    consumer: DistributionConsumer,
    wheel: Path,
    peer_id: typing.Any,
  ) -> ExtensionParticipation:
    """Finish environment mutation and its durable selection before releasing the lock."""
    name, version = release.name, release.version
    assert self.pause_peer_execution is not None
    self._environment_application_pending = True
    self._operations[name] = ("applying", None)
    try:
      await self.pause_peer_execution()
      # Other admitted Peers can change intent during the drain. This check
      # avoids known conflicts; the store still arbitrates the final write.
      await self._require_selectable(name, version, peer_id=peer_id)
      acquired = await asyncio.to_thread(
        consumer.apply, wheel, release, association, CORE_VERSION
      )
      self._validate_installed_association(acquired, association)
    except Exception as error:
      self._operations[name] = ("failed", str(error))
      raise ExtensionEnvironmentApplicationRequiredError(
        f"Applying {name}@{version} failed; the Peer remains paused: {error}"
      ) from error
    try:
      await self.store.ensure(name, release.nickname)
      selected = await self.store.select_participation(peer_id, name, "python", version)
    except Exception as error:
      self._operations[name] = ("failed", str(error))
      request_process_restart()
      raise ExtensionEnvironmentApplicationRequiredError(
        f"Applied {name}@{version}, but could not save the Peer selection; "
        f"the process is restarting to reload the changed environment: {error}"
      ) from error
    self._operations[name] = ("restarting", None)
    request_process_restart()
    return selected

  @staticmethod
  def _environment_package_versions() -> dict[str, str]:
    return {
      canonicalize_name(name): distribution.version
      for distribution in importlib.metadata.distributions()
      if (name := distribution.metadata.get("Name"))
    }

  @classmethod
  def _assert_environment_packages_preserved(
    cls,
    before: dict[str, str],
    allowed_project: str,
  ) -> None:
    after = cls._environment_package_versions()
    allowed = canonicalize_name(allowed_project)
    changed = sorted(
      name
      for name, version in before.items()
      if name != allowed and after.get(name) != version
    )
    if changed:
      raise ExtensionCompatibilityError(
        "Applying an Extension changed existing Peer environment distributions: "
        + ", ".join(changed)
      )

  @staticmethod
  def _validate_installed_association(
    acquired: AcquiredDistribution,
    association: PythonReleaseDescriptor,
  ) -> None:
    record = acquired.record
    if (
      canonicalize_name(record.python.project) != canonicalize_name(association.project)
      or record.python.entry_point.model_dump() != association.entry_point.model_dump()
      or (record.host_sdk.name, record.host_sdk.version)
      != (association.host_sdk, association.host_sdk_version)
    ):
      raise ExtensionCompatibilityError(
        "Installed Distribution differs from the exact Registry association"
      )

  async def uninstall(self, name: str, *, peer_id: typing.Any) -> None:
    validate_coordinate(name)
    if self._environment_application_pending:
      raise ExtensionStateConflictError(
        "The Peer is paused while its Python environment is being applied"
      )
    if name in self.running:
      raise ExtensionStateConflictError(f"Cannot uninstall running Extension {name}")
    await self.store.remove_participation(peer_id, name)
    try:
      await self.store.uninstall(name)
    except ExtensionStateConflictError:
      # Other Peers still select this shared identity; only this Peer’s pin was removed.
      return

  async def unselect(self, name: str, *, peer_id: typing.Any) -> None:
    validate_coordinate(name)
    async with self._runtime_lock:
      if self._environment_application_pending:
        raise ExtensionStateConflictError(
          "The Peer is paused while its Python environment is being applied"
        )
      if name in self.running:
        raise ExtensionStateConflictError(f"Cannot remove running Extension {name}")
      await self.store.remove_participation(peer_id, name)

  async def update_config(
    self,
    name: str,
    config: dict[str, typing.Any],
    *,
    peer_id: typing.Any,
  ) -> InstalledExtension:
    async with self._runtime_lock:
      return await self._update_config_locked(name, config, peer_id=peer_id)

  async def _update_config_locked(
    self,
    name: str,
    config: dict[str, typing.Any],
    *,
    peer_id: typing.Any,
  ) -> InstalledExtension:
    if self._environment_application_pending:
      raise ExtensionStateConflictError(
        "The Peer is paused while its Python environment is being applied"
      )
    state = await self.get(name)
    participation = await self.store.get_participation(peer_id, name)
    running = self.running.get(name)
    if running is None:
      if participation and participation.config_schema is not None:
        jsonschema.Draft202012Validator(participation.config_schema).validate(config)
      return await self.store.update_config(name, config)
    if participation is None or (
      participation.distribution,
      participation.version,
    ) != ("python", running.version):
      raise ExtensionRestartRequiredError(
        f"{name} is running {running.version}, which differs from this Peer’s selection"
      )
    config_class = typing.cast(
      type[sqlmodel.SQLModel], getattr(running.extension_class, "__configcls__")
    )
    validated = config_class(**config)
    await running.extension_class.update_config_async(validated)
    return await self.get(name)

  async def patch_config(
    self,
    name: str,
    patch: dict[str, typing.Any],
    *,
    peer_id: typing.Any,
  ) -> InstalledExtension:
    async with self._runtime_lock:
      if self._environment_application_pending:
        raise ExtensionStateConflictError(
          "The Peer is paused while its Python environment is being applied"
        )
      current = await self.get(name)
      return await self._update_config_locked(
        name, {**current.config, **patch}, peer_id=peer_id
      )

  async def _operation_consumers(
    self,
  ) -> tuple[ReleaseResolver, DistributionConsumer]:
    if self.release_client is not None and self.distribution_consumer is not None:
      return self.release_client, self.distribution_consumer
    origin = await self.registry_origin_resolver()
    release_client = self.release_client or RegistryReleaseClient(
      origin, settings.extension_registry_timeout_seconds
    )
    consumer = self.distribution_consumer or PipDistributionConsumer(origin)
    return release_client, consumer

  async def provision_enabled_distributions(self) -> None:
    """Apply missing exact enabled Python pins before this process starts serving."""
    peer_id = PeerManager.get_current_peer_ref()
    selections = tuple(
      row for row in await self.store.list_participations(peer_id) if row.enabled
    )
    if not selections:
      return

    unsupported = tuple(
      row for row in selections if row.distribution not in (None, "python")
    )
    if unsupported:
      identities = ", ".join(
        f"{row.name}@{row.version} ({row.distribution})" for row in unsupported
      )
      raise ExtensionCompatibilityError(
        f"This Core process cannot restore enabled distributions: {identities}"
      )

    release_client, consumer = await self._operation_consumers()
    pending: list[
      tuple[ExtensionParticipation, ExtensionReleaseDescriptor, PythonReleaseDescriptor]
    ] = []
    for selection in selections:
      await self._require_current_enabled_selection(peer_id, selection)
      release = await asyncio.to_thread(
        self._resolve,
        selection.name,
        selection.version,
        allow_yanked=True,
        release_client=release_client,
      )
      association = require_python_association(release)
      installed = await asyncio.to_thread(
        AcquiredDistribution.discover,
        selection.name,
        selection.version,
        CORE_VERSION,
      )
      if installed is not None:
        self._validate_installed_association(installed, association)
        continue
      pending.append((selection, release, association))

    if not pending:
      current_selections = tuple(
        row for row in await self.store.list_participations(peer_id) if row.enabled
      )
      if {(row.name, row.distribution, row.version) for row in current_selections} != {
        (row.name, row.distribution, row.version) for row in selections
      }:
        raise ExtensionStateConflictError(
          "Peer selections changed while enabled distributions were being restored; "
          "restart startup against the current pins"
        )
      for selection in current_selections:
        await self._require_current_enabled_selection(peer_id, selection)
      return

    # Download every exact wheel before pip can change the shared environment.
    # The process has not imported extension modules yet; it will be exec'd fresh
    # after all applications complete.
    with tempfile.TemporaryDirectory(prefix="inkcre-enabled-extensions-") as directory:
      wheel_dir = Path(directory)
      wheels: list[
        tuple[
          ExtensionParticipation,
          ExtensionReleaseDescriptor,
          PythonReleaseDescriptor,
          Path,
        ]
      ] = []
      for index, (selection, release, association) in enumerate(pending):
        await self._require_current_enabled_selection(peer_id, selection)
        selection_wheel_dir = wheel_dir / str(index)
        selection_wheel_dir.mkdir()
        wheel = await _finish_before_cancelling(
          asyncio.to_thread(
            consumer.download, release, association, CORE_VERSION, selection_wheel_dir
          )
        )
        wheels.append((selection, release, association, wheel))

      for selection, release, association, wheel in wheels:
        await self._require_current_enabled_selection(peer_id, selection)
        environment_before = self._environment_package_versions()
        acquired = await _finish_before_cancelling(
          asyncio.to_thread(consumer.apply, wheel, release, association, CORE_VERSION)
        )
        self._assert_environment_packages_preserved(environment_before, association.project)
        self._validate_installed_association(acquired, association)
        await self._require_current_enabled_selection(peer_id, selection)
        LOGGER.info(
          "Restored enabled Extension distribution %s@%s before process startup",
          selection.name,
          selection.version,
        )

    # Re-read intent after package-manager work. Do not continue on a stale plan.
    current_selections = tuple(
      row for row in await self.store.list_participations(peer_id) if row.enabled
    )
    if {(row.name, row.distribution, row.version) for row in current_selections} != {
      (row.name, row.distribution, row.version) for row in selections
    }:
      raise ExtensionStateConflictError(
        "Peer selections changed while enabled distributions were being restored; "
        "restart startup against the current pins"
      )

    # Later installs may alter shared dependencies, so verify every selected
    # project again after the complete plan has been applied.
    for selection in current_selections:
      await self._require_current_enabled_selection(peer_id, selection)
      release = await asyncio.to_thread(
        self._resolve,
        selection.name,
        selection.version,
        allow_yanked=True,
        release_client=release_client,
      )
      association = require_python_association(release)
      acquired = await asyncio.to_thread(
        AcquiredDistribution.discover,
        selection.name,
        selection.version,
        CORE_VERSION,
      )
      if acquired is None:
        raise ExtensionEnvironmentApplicationRequiredError(
          f"Applying the enabled pin {selection.name}@{selection.version} did not "
          "leave its exact distribution installed"
        )
      self._validate_installed_association(acquired, association)

  async def _require_current_enabled_selection(
    self,
    peer_id: typing.Any,
    selection: ExtensionParticipation,
  ) -> ExtensionParticipation:
    current = await self._get_current_enabled_selection(peer_id, selection)
    if current is None:
      raise ExtensionStateConflictError(
        f"Peer selection for {selection.name}@{selection.version} changed during "
        "startup restoration"
      )
    return current

  async def _acquire(self, state: InstalledExtension, selection: ExtensionParticipation):
    if selection.distribution != "python":
      raise ExtensionCompatibilityError(
        f"{state.name} is selected for an unsupported Distribution"
      )
    release_client, _consumer = await self._operation_consumers()
    release = await asyncio.to_thread(
      self._resolve,
      state.name,
      selection.version,
      allow_yanked=True,
      release_client=release_client,
    )
    association = require_python_association(release)
    loaded_version = self._loaded_versions.get(state.name)
    if loaded_version is not None and loaded_version != selection.version:
      raise ExtensionRestartRequiredError(
        f"{state.name} {loaded_version} was already imported; restart before "
        f"enabling {selection.version}"
      )
    acquired = await asyncio.to_thread(
      AcquiredDistribution.discover, state.name, selection.version, CORE_VERSION
    )
    if acquired is None:
      raise ExtensionEnvironmentApplicationRequiredError(
        f"{state.name}@{selection.version} is selected but absent from this "
        "Peer’s Python environment; "
        "apply the exact distribution while this Peer is paused, then start a new process"
      )
    self._validate_installed_association(acquired, association)
    return association, acquired

  async def _start(
    self,
    app: fastapi.FastAPI,
    state: InstalledExtension,
    selection: ExtensionParticipation,
  ) -> RunningExtension:
    existing = self.running.get(state.name)
    if existing is not None:
      if existing.version != selection.version:
        raise ExtensionRestartRequiredError(
          f"A different Release of {state.name} is already running"
        )
      return existing
    association, acquired = await self._acquire(state, selection)
    return await self._start_acquired(app, state, selection, association, acquired)

  async def _start_acquired(
    self,
    app: fastapi.FastAPI,
    state: InstalledExtension,
    selection: ExtensionParticipation,
    association: PythonReleaseDescriptor,
    acquired: AcquiredDistribution,
  ) -> RunningExtension:
    try:
      claim = ExtensionRuntimeClaim.acquire(association.entry_point.name)
    except ExtensionRuntimeClaimConflictError as error:
      raise ExtensionStateConflictError(str(error)) from error
    try:
      modules = DistributionModules(acquired)
    except Exception:
      claim.release()
      raise
    extension_class: type[ExtensionBase] | None = None
    try:
      # Import is irreversible even when the module or its startup later fails.
      # A different selected version therefore still needs a fresh process.
      self._loaded_versions[state.name] = selection.version
      extension_class = typing.cast(type[ExtensionBase], modules.load(ExtensionBase))
      if extension_class.__extid__ != association.entry_point.name:
        raise ExtensionCompatibilityError(
          "ExtensionBase identity differs from the declared entry point"
        )
      extension_class.bind(
        _ActiveExtensionModel(
          name=state.name,
          config=dict(state.config),
          store=self.store,
          peer_id=selection.peer_id,
          distribution=typing.cast(str, selection.distribution),
          version=selection.version,
        )
      )
      await extension_class.on_start_async(app)
      modules.assert_origins()
    except BaseException:
      if extension_class is not None:
        with contextlib.suppress(Exception):
          await extension_class.on_close()
        with contextlib.suppress(Exception):
          extension_class.unpublish()
        with contextlib.suppress(Exception):
          extension_class.unbind()
        with contextlib.suppress(Exception):
          extension_class.release_runtime()
      claim.release()
      raise
    running = RunningExtension(
      name=state.name,
      version=selection.version,
      association=association,
      acquired=acquired,
      extension_class=extension_class,
      claim=claim,
    )
    self.running[state.name] = running
    return running

  async def _get_current_enabled_selection(
    self,
    peer_id: typing.Any,
    selection: ExtensionParticipation,
  ) -> ExtensionParticipation | None:
    current = await self.store.get_participation(peer_id, selection.name)
    if current is None or (
      current.distribution,
      current.version,
      current.enabled,
    ) != (selection.distribution, selection.version, True):
      return None
    return current

  async def _stop(self, running: RunningExtension) -> None:
    await running.extension_class.on_close()
    running.extension_class.unpublish()
    running.extension_class.unbind()
    running.extension_class.release_runtime()
    running.claim.release()
    self.running.pop(running.name, None)

  async def _force_stop(self, running: RunningExtension) -> typing.List[Exception]:
    failures: list[Exception] = []
    try:
      await running.extension_class.on_close()
    except Exception as error:
      failures.append(error)
    try:
      running.extension_class.unpublish()
    except Exception as error:
      failures.append(error)
    try:
      running.extension_class.unbind()
    except Exception as error:
      failures.append(error)
    running.extension_class.release_runtime()
    running.claim.release()
    self.running.pop(running.name, None)
    return failures

  async def enable(
    self,
    name: str,
    *,
    peer_id: typing.Any,
    app: fastapi.FastAPI | None = None,
  ) -> ExtensionParticipation:
    validate_coordinate(name)
    if self._environment_application_pending:
      raise ExtensionStateConflictError(
        "The Peer is paused while its Python environment is being applied"
      )
    runtime_app = app or self.fastapi_app
    if runtime_app is None:
      raise ExtensionRuntimeError("FastAPI app is required to enable an Extension")
    self.fastapi_app = runtime_app
    async with self._runtime_lock:
      if self._environment_application_pending:
        raise ExtensionStateConflictError(
          "The Peer is paused while its Python environment is being applied"
        )
      state = await self.get(name)
      selection = await self.store.get_participation(peer_id, name)
      if selection is None:
        raise ExtensionNotInstalledError(f"{name} has no selection on this Peer")
      if selection.distribution is None:
        raise ExtensionStateConflictError(
          f"{name} has unresolved legacy distribution; select its exact distribution first"
        )
      if not selection.enabled:
        selection = await self.store.set_participation_enabled(peer_id, name, True)
      current = await self._get_current_enabled_selection(peer_id, selection)
      if current is None:
        raise ExtensionStateConflictError(
          f"{name} selection or enabled intent changed before startup"
        )
      selection = current
      try:
        running = await self._start(runtime_app, state, selection)
      except Exception as error:
        self._operations[name] = (None, str(error))
        raise
      publishes_peer_inbounds = bool(running.extension_class.peer_inbounds())
      if publishes_peer_inbounds:
        await PeerManager.refresh_self(settings.peer_lease_ttl_seconds)
      current = await self._get_current_enabled_selection(peer_id, selection)
      if current is None:
        failures = await self._force_stop(running)
        if publishes_peer_inbounds:
          try:
            await PeerManager.refresh_self(settings.peer_lease_ttl_seconds)
          except Exception as error:
            failures.append(error)
        conflict = ExtensionStateConflictError(
          f"{name} selection or enabled intent changed while startup was running"
        )
        self._operations[name] = (None, str(conflict))
        if failures:
          raise BaseExceptionGroup(
            "Extension startup intent changed and runtime cleanup failed",
            [conflict, *failures],
          )
        raise conflict
      self._operations[name] = (None, None)
      return current

  async def disable(self, name: str, *, peer_id: typing.Any) -> ExtensionParticipation:
    validate_coordinate(name)
    async with self._runtime_lock:
      if self._environment_application_pending:
        raise ExtensionStateConflictError(
          "The Peer is paused while its Python environment is being applied"
        )
      selection = await self.store.get_participation(peer_id, name)
      if selection is None:
        raise ExtensionNotInstalledError(f"{name} has no selection on this Peer")
      if not selection.enabled:
        return selection
      running = self.running.get(name)
      published = bool(running and running.extension_class.peer_inbounds())
      if running is not None:
        await self._stop(running)
      persisted = await self.store.set_participation_enabled(peer_id, name, False)
      if published:
        await PeerManager.refresh_self(settings.peer_lease_ttl_seconds)
      return persisted

  async def start_enabled(self, app: fastapi.FastAPI) -> None:
    """Cold restore exact Python selections; never install packages in this process."""
    self.fastapi_app = app
    peer_id = PeerManager.get_current_peer_ref()
    failures: list[Exception] = []
    async with self._runtime_lock:
      for selection in await self.store.list_participations(peer_id):
        if not selection.enabled:
          continue
        try:
          if await self._get_current_enabled_selection(peer_id, selection) is None:
            continue
          if selection.distribution is None:
            resolver, _ = await self._operation_consumers()
            legacy_release = await asyncio.to_thread(
              self._resolve,
              selection.name,
              selection.version,
              allow_yanked=True,
              release_client=resolver,
            )
            require_python_association(legacy_release)
            selection = await self.store.resolve_legacy_python_participation(
              peer_id, selection.name, selection.version
            )
          if selection.distribution != "python":
            raise ExtensionCompatibilityError(
              f"Core cannot cold restore {selection.name} {selection.distribution}"
            )
          running = await self._start(app, await self.get(selection.name), selection)
          if await self._get_current_enabled_selection(peer_id, selection) is None:
            cleanup_failures = await self._force_stop(running)
            if cleanup_failures:
              raise ExceptionGroup(
                f"Cold restore intent changed and cleanup failed for {selection.name}",
                cleanup_failures,
              )
            continue
          self._operations[selection.name] = (None, None)
        except Exception as error:
          self._operations[selection.name] = (None, str(error))
          LOGGER.exception("Cold restore failed for %s", selection.name)
          error.add_note(f"Cold restore failed for {selection.name}@{selection.version}")
          failures.append(error)
    if failures:
      raise ExceptionGroup("Enabled Extensions failed to restore", failures)

  async def close_running(self) -> None:
    async with self._runtime_lock:
      await self._close_running_locked()

  async def close_running_for_environment_application(self) -> None:
    """Close extensions while select owns the lock and fences other mutations."""
    await self._close_running_locked()

  async def _close_running_locked(self) -> None:
    failures: list[Exception] = []
    for running in tuple(self.running.values()):
      try:
        await self._stop(running)
      except Exception as error:
        failures.append(error)
    if len(failures) == 1:
      raise failures[0]
    if failures:
      raise ExceptionGroup("Extension shutdown failed", failures)


EXTENSION_HOST = ExtensionHost()


__all__ = [
  "EXTENSION_MANAGEMENT_CAPABILITY",
  "EXTENSION_HOST",
  "EmptyConfig",
  "ExtensionBase",
  "ExtensionDelegationError",
  "ExtensionHost",
  "ExtensionHostError",
  "InstalledExtension",
  "PublicHTTPRoute",
]
