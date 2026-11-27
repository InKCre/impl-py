from pathlib import Path
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

  def download(self, descriptor, _association, _host_version, wheel_dir: Path) -> Path:
    self.downloads.append((descriptor.name, descriptor.version))
    self.download_dirs.append(wheel_dir)
    if self.on_download is not None:
      self.on_download(descriptor)
    return wheel_dir / f"{descriptor.name.rsplit('/', 1)[-1]}-{descriptor.version}.whl"

  def apply(self, _wheel, descriptor, _association, _host_version):
    key = (descriptor.name, descriptor.version)
    self.applications.append(key)
    acquired = acquired_distribution(descriptor)
    self.installed[key] = acquired
    if self.on_apply is not None:
      self.on_apply(descriptor)
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


@pytest.mark.parametrize("selection_case", ["python", "legacy", "disabled", "empty"])
def test_startup_provisions_only_enabled_exact_python_selections(
  async_runner,
  monkeypatch: pytest.MonkeyPatch,
  selection_case: str,
) -> None:
  selected = participation(
    "inkcre/mail",
    "0.3.3",
    enabled=selection_case != "disabled",
    distribution=None if selection_case == "legacy" else "python",
  )
  disabled = participation("inkcre/rss", "0.2.2", enabled=False)
  resolver = ReleaseReader(
    {
      ("inkcre/mail", "0.3.3"): release("inkcre/mail", "0.3.3"),
    }
  )
  consumer = DistributionConsumer()
  selections = [] if selection_case == "empty" else [selected, disabled]
  store = SelectionStore(*selections)
  host = ExtensionHost(
    store=typing.cast(ExtensionStore, store),
    release_client=typing.cast(typing.Any, resolver),
    distribution_consumer=typing.cast(typing.Any, consumer),
  )

  def discover(name: str, version: str, _host_version: str):
    return consumer.installed.get((name, version))

  monkeypatch.setattr(extension_main.AcquiredDistribution, "discover", discover)
  async_runner.run(host.provision_enabled_distributions())

  expected = [] if selection_case in {"empty", "disabled"} else [("inkcre/mail", "0.3.3")]
  assert set(resolver.lookups) == set(expected)
  assert consumer.downloads == expected
  assert consumer.applications == expected
  assert store.selections == selections


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


def test_container_web_checks_readiness_and_provisions_before_exec(
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


def test_environment_application_rejects_runtime_dependency_downgrade(
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
