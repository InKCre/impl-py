import asyncio
from pathlib import Path
import threading
import typing
import uuid

import pytest
from inkcre_extension_runtime_core_py.generated.registry import (
  Distribution,
  NativeReleaseRecord,
  State,
)

from app.business.extension import main as extension_main
from app.business.extension.errors import ExtensionCompatibilityError
from app.business.extension.main import ExtensionHost
from app.business.extension.state import (
  ExtensionParticipation,
  ExtensionStore,
  InstalledExtension,
)


PEER_ID = uuid.UUID("00000000-0000-4000-8000-000000000118")


def release(name: str, version: str) -> NativeReleaseRecord:
  leaf = name.rsplit("/", 1)[-1].replace("-", "_")
  project = f"inkcre-ext-{leaf.replace('_', '-')}"
  module = f"inkcre_ext_{leaf}"
  return NativeReleaseRecord(
    name=name,
    nickname=leaf.title(),
    version=version,
    state=State.published,
    distribution=Distribution.python,
    python={
      "project": project,
      "host_sdk": "core-py",
      "host_sdk_version": ">=0.2.0 <0.4.0",
      "entry_point": {
        "group": "inkcre.core.extensions",
        "name": leaf,
        "object": f"{module}:{leaf.title()}",
      },
      "simple_url": f"https://registry.example/simple/{project}/",
    },
  )


class SelectionStore:
  def __init__(self, *selections: ExtensionParticipation) -> None:
    self.selections = list(selections)

  async def list_participations(self, peer_id: uuid.UUID):
    assert peer_id == PEER_ID
    return tuple(self.selections)

  async def get_participation(self, peer_id: uuid.UUID, name: str):
    assert peer_id == PEER_ID
    return next((row for row in self.selections if row.name == name), None)

  async def get(self, name: str):
    return InstalledExtension(name=name)

  async def list(self):
    return tuple(InstalledExtension(name=row.name) for row in self.selections)

  async def set_participation_enabled(self, peer_id, name, enabled):
    current = await self.get_participation(peer_id, name)
    assert current is not None
    selected = current.model_copy(update={"enabled": enabled})
    self.selections = [selected if row.name == name else row for row in self.selections]
    return selected

  async def ensure(self, name: str, nickname: str):
    return InstalledExtension(name=name, nickname=nickname)

  async def select_participation(
    self, peer_id: uuid.UUID, name: str, distribution: str, version: str
  ):
    current = await self.get_participation(peer_id, name)
    if current is not None and current.enabled:
      raise extension_main.ExtensionStateConflictError("selection is enabled")
    selected = participation(name, version, enabled=False)
    self.selections = [row for row in self.selections if row.name != name]
    self.selections.append(selected)
    return selected

  async def remove_participation(self, peer_id: uuid.UUID, name: str):
    assert peer_id == PEER_ID
    self.selections = [row for row in self.selections if row.name != name]

  async def resolve_legacy_python_participation(
    self, peer_id: uuid.UUID, name: str, version: str
  ):
    assert peer_id == PEER_ID
    for index, row in enumerate(self.selections):
      if row.name != name:
        continue
      if (row.distribution, row.version, row.enabled) != (None, version, True):
        raise extension_main.ExtensionStateConflictError(
          f"{name} legacy selection changed before its Python distribution was resolved"
        )
      resolved = row.model_copy(update={"distribution": "python", "config_schema": None})
      self.selections[index] = resolved
      return resolved
    raise extension_main.ExtensionStateConflictError(f"{name} selection disappeared")


class ReleaseReader:
  def __init__(self, releases: dict[tuple[str, str], NativeReleaseRecord]) -> None:
    self.releases = releases
    self.lookups: list[tuple[str, str]] = []

  def get(self, name: str, version: str) -> NativeReleaseRecord:
    self.lookups.append((name, version))
    return self.releases[(name, version)]


class DistributionConsumer:
  def __init__(self) -> None:
    self.downloads: list[tuple[str, str]] = []
    self.download_dirs: list[Path] = []
    self.applications: list[tuple[str, str]] = []
    self.installed: dict[tuple[str, str], typing.Any] = {}
    self.on_download = None
    self.on_apply = None

  def download(self, release, association, host_version, wheel_dir: Path) -> Path:
    self.downloads.append((release.name, release.version))
    self.download_dirs.append(wheel_dir)
    if self.on_download is not None:
      self.on_download(release)
    return wheel_dir / f"{release.name.rsplit('/', 1)[-1]}-{release.version}.whl"

  def apply(self, extension_wheel, release, association, host_version):
    key = (release.name, release.version)
    self.applications.append(key)
    acquired = acquired_distribution(release)
    self.installed[key] = acquired
    if self.on_apply is not None:
      self.on_apply(release)
    return acquired


def acquired_distribution(descriptor):
  association = descriptor.python
  return typing.cast(
    typing.Any,
    type(
      "Acquired",
      (),
      {
        "record": type(
          "InstalledRecord",
          (),
          {
            "python": association,
            "host_sdk": type(
              "HostSdk",
              (),
              {"name": "core-py", "version": association.host_sdk_version},
            )(),
          },
        )(),
      },
    )(),
  )


def participation(
  name: str,
  version: str,
  *,
  enabled: bool,
  distribution: typing.Literal["python", "module_federation"] | None = "python",
) -> ExtensionParticipation:
  return ExtensionParticipation(
    name=name,
    peer_id=PEER_ID,
    distribution=distribution,
    version=version,
    enabled=enabled,
  )


@pytest.fixture(autouse=True)
def current_peer(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(
    extension_main.PeerManager,
    "get_current_peer_ref",
    staticmethod(lambda: PEER_ID),
  )


@pytest.mark.parametrize("distribution", ["python", None])
def test_startup_provisions_only_enabled_exact_python_selections(
  async_runner,
  monkeypatch: pytest.MonkeyPatch,
  distribution: typing.Literal["python"] | None,
) -> None:
  selected = participation("inkcre/mail", "0.3.3", enabled=True, distribution=distribution)
  disabled = participation("inkcre/rss", "0.2.2", enabled=False)
  resolver = ReleaseReader(
    {
      ("inkcre/mail", "0.3.3"): release("inkcre/mail", "0.3.3"),
    }
  )
  consumer = DistributionConsumer()
  store = SelectionStore(selected, disabled)
  host = ExtensionHost(
    store=typing.cast(ExtensionStore, store),
    release_client=typing.cast(typing.Any, resolver),
    distribution_consumer=typing.cast(typing.Any, consumer),
  )

  def discover(name: str, version: str, _host_version: str):
    return consumer.installed.get((name, version))

  monkeypatch.setattr(extension_main.AcquiredDistribution, "discover", discover)
  async_runner.run(host.provision_enabled_distributions())

  assert resolver.lookups == [("inkcre/mail", "0.3.3"), ("inkcre/mail", "0.3.3")]
  assert consumer.downloads == [("inkcre/mail", "0.3.3")]
  assert consumer.applications == [("inkcre/mail", "0.3.3")]
  assert store.selections == [selected, disabled]


@pytest.mark.parametrize("change_during", ["download", "apply"])
def test_startup_stops_when_exact_enabled_pin_changes_during_restore(
  async_runner,
  monkeypatch: pytest.MonkeyPatch,
  change_during: typing.Literal["download", "apply"],
) -> None:
  selected = participation("inkcre/mail", "0.3.3", enabled=True)
  replacement = participation("inkcre/mail", "0.3.2", enabled=True)
  store = SelectionStore(selected)
  consumer = DistributionConsumer()
  setattr(
    consumer,
    f"on_{change_during}",
    lambda _descriptor: store.selections.__setitem__(0, replacement),
  )
  resolver = ReleaseReader({("inkcre/mail", "0.3.3"): release("inkcre/mail", "0.3.3")})
  host = ExtensionHost(
    store=typing.cast(ExtensionStore, store),
    release_client=typing.cast(typing.Any, resolver),
    distribution_consumer=typing.cast(typing.Any, consumer),
  )
  monkeypatch.setattr(
    extension_main.AcquiredDistribution,
    "discover",
    lambda name, version, _host_version: consumer.installed.get((name, version)),
  )

  with pytest.raises(extension_main.ExtensionStateConflictError, match="changed"):
    async_runner.run(host.provision_enabled_distributions())

  assert store.selections == [replacement]
  assert consumer.applications == (
    [] if change_during == "download" else [("inkcre/mail", "0.3.3")]
  )


def test_cold_restore_resolves_only_the_exact_enabled_legacy_pin(
  async_runner,
) -> None:
  selected = participation("inkcre/mail", "0.3.3", enabled=True, distribution=None)
  store = SelectionStore(selected)
  resolver = ReleaseReader({("inkcre/mail", "0.3.3"): release("inkcre/mail", "0.3.3")})
  host = ExtensionHost(
    store=typing.cast(ExtensionStore, store),
    release_client=typing.cast(typing.Any, resolver),
    distribution_consumer=typing.cast(typing.Any, DistributionConsumer()),
  )
  started: list[ExtensionParticipation] = []

  async def start(_app, _extension, selection):
    started.append(selection)
    return typing.cast(typing.Any, object())

  typing.cast(typing.Any, host)._start = start
  async_runner.run(host.start_enabled(typing.cast(typing.Any, object())))

  assert resolver.lookups == [("inkcre/mail", "0.3.3")]
  assert len(started) == 1
  assert (started[0].distribution, started[0].version, started[0].enabled) == (
    "python",
    "0.3.3",
    True,
  )
  assert len(store.selections) == 1
  assert (
    store.selections[0].distribution,
    store.selections[0].version,
    store.selections[0].enabled,
  ) == ("python", "0.3.3", True)


def test_startup_rejects_enabled_module_federation_without_mutation(async_runner) -> None:
  selected = participation(
    "inkcre/mail", "0.3.3", enabled=True, distribution="module_federation"
  )
  store = SelectionStore(selected)
  host = ExtensionHost(
    store=typing.cast(ExtensionStore, store),
    release_client=typing.cast(typing.Any, ReleaseReader({})),
    distribution_consumer=typing.cast(typing.Any, DistributionConsumer()),
  )

  with pytest.raises(ExtensionCompatibilityError, match="module_federation"):
    async_runner.run(host.provision_enabled_distributions())

  assert store.selections == [selected]


def test_startup_downloads_multiple_pins_into_isolated_directories(
  async_runner, monkeypatch
):
  mail = participation("inkcre/mail", "0.3.3", enabled=True)
  rss = participation("inkcre/rss", "0.2.2", enabled=True)
  resolver = ReleaseReader(
    {
      ("inkcre/mail", "0.3.3"): release("inkcre/mail", "0.3.3"),
      ("inkcre/rss", "0.2.2"): release("inkcre/rss", "0.2.2"),
    }
  )
  consumer = DistributionConsumer()
  host = ExtensionHost(
    store=typing.cast(ExtensionStore, SelectionStore(mail, rss)),
    release_client=typing.cast(typing.Any, resolver),
    distribution_consumer=typing.cast(typing.Any, consumer),
  )
  monkeypatch.setattr(
    extension_main.AcquiredDistribution,
    "discover",
    lambda name, version, _host_version: consumer.installed.get((name, version)),
  )

  async_runner.run(host.provision_enabled_distributions())

  assert consumer.downloads == [
    ("inkcre/mail", "0.3.3"),
    ("inkcre/rss", "0.2.2"),
  ]
  assert len(set(consumer.download_dirs)) == 2
  assert consumer.applications == consumer.downloads


def test_container_web_provisions_before_exec_and_keeps_ready_read_only(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import app.database_contract.readiness as readiness
  import scripts.container as container
  from app.business.extension.main import EXTENSION_HOST

  events: list[str] = []

  class Ready:
    ready = True
    reason = "ready"

  async def provision() -> None:
    events.append("provision")

  def exec_process(_executable: str, _arguments: list[str]) -> None:
    events.append("exec")
    raise RuntimeError("process replaced")

  def check_ready() -> Ready:
    events.append("ready")
    return Ready()

  monkeypatch.setattr(readiness, "check_database_contract", check_ready)
  monkeypatch.setattr(EXTENSION_HOST, "provision_enabled_distributions", provision)
  monkeypatch.setattr(container.os, "execvp", exec_process)
  monkeypatch.setenv("PORT", "8000")

  with pytest.raises(RuntimeError, match="process replaced"):
    container._web()

  assert events == ["ready", "provision", "exec"]


def test_startup_provision_does_not_touch_database_when_no_pins(async_runner) -> None:
  host = ExtensionHost(store=typing.cast(ExtensionStore, SelectionStore()))
  async_runner.run(host.provision_enabled_distributions())


def test_environment_application_rejects_core_runtime_or_base_downgrade(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  snapshots = iter(
    [
      {
        "inkcre-core": "0.7.2",
        "inkcre-extension-runtime-core-py": "0.2.0",
        "fastapi": "0.139.2",
      },
      {
        "inkcre-core": "0.7.2",
        "inkcre-extension-runtime-core-py": "0.1.5",
        "fastapi": "0.139.2",
        "inkcre-ext-mail": "0.3.3",
      },
    ]
  )
  monkeypatch.setattr(
    ExtensionHost,
    "_environment_package_versions",
    classmethod(lambda _cls: next(snapshots)),
  )

  with pytest.raises(ExtensionCompatibilityError, match="inkcre-extension-runtime-core-py"):
    ExtensionHost._assert_environment_packages_preserved(
      ExtensionHost._environment_package_versions(), "inkcre-ext-mail"
    )


def test_select_rejects_enabled_replacement_before_registry_or_environment_work(
  async_runner, monkeypatch
):
  store = SelectionStore(participation("inkcre/mail", "0.3.2", enabled=True))
  reader = ReleaseReader({("inkcre/mail", "0.3.3"): release("inkcre/mail", "0.3.3")})
  consumer = DistributionConsumer()
  host = ExtensionHost(
    store=typing.cast(ExtensionStore, store),
    release_client=reader,
    distribution_consumer=consumer,
  )
  monkeypatch.setattr(extension_main.AcquiredDistribution, "discover", lambda *_: None)

  with pytest.raises(extension_main.ExtensionStateConflictError, match="enabled"):
    async_runner.run(host.select("inkcre/mail", "python", "0.3.3", peer_id=PEER_ID))

  assert reader.lookups == []
  assert consumer.applications == []
  assert store.selections[0].version == "0.3.2"


def test_select_rechecks_enabled_intent_after_peer_drain(async_runner, monkeypatch):
  store = SelectionStore(participation("inkcre/mail", "0.3.2", enabled=False))
  reader = ReleaseReader({("inkcre/mail", "0.3.3"): release("inkcre/mail", "0.3.3")})
  consumer = DistributionConsumer()
  host = ExtensionHost(
    store=typing.cast(ExtensionStore, store),
    release_client=reader,
    distribution_consumer=consumer,
  )
  monkeypatch.setattr(extension_main.AcquiredDistribution, "discover", lambda *_: None)

  async def pause():
    store.selections[0] = store.selections[0].model_copy(update={"enabled": True})

  host.pause_peer_execution = pause
  with pytest.raises(extension_main.ExtensionEnvironmentApplicationRequiredError):
    async_runner.run(host.select("inkcre/mail", "python", "0.3.3", peer_id=PEER_ID))

  assert consumer.applications == []
  status = async_runner.run(host.status(PEER_ID))
  assert status.extensions[0].operation == "failed"
  assert "enabled" in (status.extensions[0].error or "")


def test_cancelled_download_keeps_directory_and_serializes_until_worker_finishes(
  async_runner, monkeypatch
):
  async def scenario():
    entered = asyncio.Event()
    release_worker = threading.Event()
    loop = asyncio.get_running_loop()
    store = SelectionStore(participation("inkcre/mail", "0.3.2", enabled=False))
    reader = ReleaseReader({("inkcre/mail", "0.3.3"): release("inkcre/mail", "0.3.3")})

    class Consumer(DistributionConsumer):
      def download(self, release, association, host_version, wheel_dir):
        wheel = super().download(release, association, host_version, wheel_dir)
        loop.call_soon_threadsafe(entered.set)
        if not release_worker.wait(10):
          raise TimeoutError("test did not release download")
        # Writing after cancellation detects premature TemporaryDirectory cleanup.
        wheel.write_bytes(b"download completed")
        return wheel

    consumer = Consumer()
    host = ExtensionHost(
      store=typing.cast(ExtensionStore, store),
      release_client=reader,
      distribution_consumer=consumer,
    )

    async def pause():
      raise AssertionError("a cancelled download must not pause the Peer")

    host.pause_peer_execution = pause
    monkeypatch.setattr(extension_main.AcquiredDistribution, "discover", lambda *_: None)
    task = asyncio.create_task(
      host.select("inkcre/mail", "python", "0.3.3", peer_id=PEER_ID)
    )
    await entered.wait()
    removal = asyncio.create_task(host.unselect("inkcre/mail", peer_id=PEER_ID))
    try:
      task.cancel()
      await asyncio.sleep(0)
      task.cancel()
      await asyncio.sleep(0)
      assert not task.done()
      assert not removal.done()
    finally:
      release_worker.set()
      with pytest.raises(asyncio.CancelledError):
        await task
      await removal
    assert not consumer.download_dirs[0].exists()
    assert consumer.applications == []

  async_runner.run(scenario())


@pytest.mark.parametrize(
  ("phase", "fails"),
  [("pause", False), ("apply", False), ("save", False), ("apply", True), ("save", True)],
)
def test_cancelled_application_reaches_an_observable_terminal_state(
  async_runner, monkeypatch, phase, fails
):
  async def scenario():
    entered, resume = asyncio.Event(), asyncio.Event()
    release_worker = threading.Event()
    loop = asyncio.get_running_loop()
    restarted: list[bool] = []

    class Store(SelectionStore):
      async def select_participation(self, peer_id, name, distribution, version):
        if phase == "save":
          entered.set()
          await resume.wait()
          if fails:
            raise RuntimeError("selection write unavailable")
        return await super().select_participation(peer_id, name, distribution, version)

    class Consumer(DistributionConsumer):
      def download(self, release, association, host_version, wheel_dir):
        wheel = super().download(release, association, host_version, wheel_dir)
        wheel.write_bytes(b"wheel")
        return wheel

      def apply(self, extension_wheel, release, association, host_version):
        if phase == "apply":
          loop.call_soon_threadsafe(entered.set)
          if not release_worker.wait(10):
            raise TimeoutError("test did not release apply")
        assert extension_wheel.read_bytes() == b"wheel"
        if fails and phase == "apply":
          raise RuntimeError("package application failed")
        return super().apply(extension_wheel, release, association, host_version)

    store = Store(participation("inkcre/mail", "0.3.2", enabled=False))
    reader = ReleaseReader({("inkcre/mail", "0.3.3"): release("inkcre/mail", "0.3.3")})
    consumer = Consumer()
    host = ExtensionHost(
      store=typing.cast(ExtensionStore, store),
      release_client=reader,
      distribution_consumer=consumer,
    )

    async def pause():
      if phase == "pause":
        entered.set()
        await resume.wait()

    host.pause_peer_execution = pause
    monkeypatch.setattr(
      extension_main, "request_process_restart", lambda: restarted.append(True)
    )
    monkeypatch.setattr(extension_main.AcquiredDistribution, "discover", lambda *_: None)
    task = asyncio.create_task(
      host.select("inkcre/mail", "python", "0.3.3", peer_id=PEER_ID)
    )
    await entered.wait()
    try:
      task.cancel()
      await asyncio.sleep(0)
      task.cancel()
      await asyncio.sleep(0)
      assert not task.done()
    finally:
      resume.set()
      release_worker.set()
      with pytest.raises(asyncio.CancelledError):
        await task
    status = await host.status(PEER_ID)
    if fails:
      assert store.selections[0].version == "0.3.2"
      assert status.extensions[0].operation == "failed"
      assert status.extensions[0].error
      assert restarted == ([True] if phase == "save" else [])
    else:
      assert store.selections[0].version == "0.3.3"
      assert status.extensions[0].operation == "restarting"
      assert restarted == [True]
    assert not consumer.download_dirs[0].exists()

  async_runner.run(scenario())


def test_cancelled_cold_restore_finishes_current_apply_without_starting_next(
  async_runner, monkeypatch
):
  async def scenario():
    entered = asyncio.Event()
    resume = threading.Event()
    loop = asyncio.get_running_loop()
    selected = (
      participation("inkcre/mail", "0.3.3", enabled=True),
      participation("inkcre/rss", "0.2.2", enabled=True),
    )
    reader = ReleaseReader(
      {(row.name, row.version): release(row.name, row.version) for row in selected}
    )
    consumer = DistributionConsumer()

    def on_apply(_descriptor):
      loop.call_soon_threadsafe(entered.set)
      if not resume.wait(10):
        raise TimeoutError("test did not release apply")
      assert all(directory.exists() for directory in consumer.download_dirs)

    consumer.on_apply = on_apply
    host = ExtensionHost(
      store=typing.cast(ExtensionStore, SelectionStore(*selected)),
      release_client=reader,
      distribution_consumer=consumer,
    )
    monkeypatch.setattr(extension_main.AcquiredDistribution, "discover", lambda *_: None)
    task = asyncio.create_task(host.provision_enabled_distributions())
    await entered.wait()
    try:
      task.cancel()
      await asyncio.sleep(0)
      assert not task.done()
    finally:
      resume.set()
      with pytest.raises(asyncio.CancelledError):
        await task
    assert consumer.applications == [("inkcre/mail", "0.3.3")]
    assert all(not directory.exists() for directory in consumer.download_dirs)

  async_runner.run(scenario())


def test_failed_import_still_requires_restart_to_select_a_different_version(
  async_runner, monkeypatch
):
  name = "inkcre/failed-import"
  store = SelectionStore(participation(name, "1.0.0", enabled=False))
  releases = {(name, version): release(name, version) for version in ("1.0.0", "1.1.0")}
  reader = ReleaseReader(releases)
  restarted: list[bool] = []

  class BrokenModule:
    def __init__(self, _acquired):
      pass

    def load(self, _base):
      raise RuntimeError("partial import failed")

  host = ExtensionHost(
    store=typing.cast(ExtensionStore, store),
    release_client=reader,
    distribution_consumer=DistributionConsumer(),
  )
  monkeypatch.setattr(extension_main, "DistributionModules", BrokenModule)
  monkeypatch.setattr(
    extension_main.AcquiredDistribution,
    "discover",
    lambda name, version, _host: acquired_distribution(releases[(name, version)]),
  )
  monkeypatch.setattr(
    extension_main, "request_process_restart", lambda: restarted.append(True)
  )

  async def scenario():
    with pytest.raises(RuntimeError, match="partial import"):
      await host.enable(name, peer_id=PEER_ID, app=extension_main.fastapi.FastAPI())
    await host.disable(name, peer_id=PEER_ID)
    await host.select(name, "python", "1.1.0", peer_id=PEER_ID)
    status = await host.status(PEER_ID)
    assert status.extensions[0].loaded_version == "1.0.0"
    assert status.extensions[0].restart_required
    assert not status.extensions[0].running

  async_runner.run(scenario())
  assert restarted == [True]


@pytest.mark.parametrize("operation", ["startup_failure", "disable", "intent_conflict"])
def test_runtime_effect_cleanup_preserves_each_operation_error_policy(
  async_runner, monkeypatch, operation
):
  name = "inkcre/cleanup-policy"
  descriptor = release(name, "1.0.0")
  store = SelectionStore(participation(name, "1.0.0", enabled=False))
  resources = {"bound": False, "published": False, "runtime_owned": True}
  fail_close = True

  class Extension:
    __extid__ = "cleanup_policy"

    @classmethod
    def bind(cls, model):
      resources["bound"] = True

    @classmethod
    async def on_start_async(cls, app):
      resources["published"] = True
      if operation == "startup_failure":
        raise ValueError("startup error")
      if operation == "intent_conflict":
        store.selections[0] = store.selections[0].model_copy(update={"enabled": False})

    @classmethod
    async def on_close(cls):
      if fail_close:
        raise RuntimeError("close error")

    @classmethod
    def unpublish(cls):
      resources["published"] = False

    @classmethod
    def unbind(cls):
      resources["bound"] = False

    @classmethod
    def release_runtime(cls):
      resources["runtime_owned"] = False

    @classmethod
    def peer_inbounds(cls):
      return ()

  class Modules:
    def __init__(self, acquired):
      pass

    def load(self, base):
      return Extension

    def assert_origins(self):
      pass

  host = ExtensionHost(
    store=typing.cast(ExtensionStore, store),
    release_client=ReleaseReader({(name, "1.0.0"): descriptor}),
    distribution_consumer=DistributionConsumer(),
  )
  monkeypatch.setattr(extension_main, "DistributionModules", Modules)
  monkeypatch.setattr(
    extension_main.AcquiredDistribution,
    "discover",
    lambda *_: acquired_distribution(descriptor),
  )

  async def scenario():
    nonlocal fail_close
    try:
      if operation == "startup_failure":
        with pytest.raises(ValueError, match="startup error"):
          await host.enable(name, peer_id=PEER_ID, app=extension_main.fastapi.FastAPI())
      elif operation == "intent_conflict":
        with pytest.raises(BaseExceptionGroup) as caught:
          await host.enable(name, peer_id=PEER_ID, app=extension_main.fastapi.FastAPI())
        assert isinstance(
          caught.value.exceptions[0], extension_main.ExtensionStateConflictError
        )
        assert str(caught.value.exceptions[1]) == "close error"
      else:
        await host.enable(name, peer_id=PEER_ID, app=extension_main.fastapi.FastAPI())
        with pytest.raises(RuntimeError, match="close error"):
          await host.disable(name, peer_id=PEER_ID)
        assert name in host.running
        assert store.selections[0].enabled
        assert all(resources.values())
        return
      assert not any(resources.values())
      assert not host.running
      claim = extension_main.ExtensionRuntimeClaim.acquire("cleanup_policy")
      claim.release()
    finally:
      fail_close = False
      await host.close_running()

  async_runner.run(scenario())
