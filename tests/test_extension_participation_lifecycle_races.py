"""Persisted Extension intent and local runtime effects have separate lifetimes."""

from types import SimpleNamespace
import uuid
import typing

import fastapi
import pytest

from app.business.extension import main as extension_main
from app.business.extension.errors import ExtensionStateConflictError
from app.business.extension.main import ExtensionHost
from app.business.extension.state import (
  ExtensionParticipation,
  ExtensionStore,
  InstalledExtension,
)
from inkcre_core.schemas.extension import DisableExtensionCommand, EnableExtensionCommand


class ParticipationStore:
  def __init__(self, participation: ExtensionParticipation) -> None:
    self.participation = participation
    self.extension = InstalledExtension(name=participation.name, nickname="Race test")
    self.writes: list[bool] = []
    self.write_error: Exception | None = None

  async def list(self) -> tuple[InstalledExtension, ...]:
    return (self.extension,)

  async def get(self, _name: str) -> InstalledExtension:
    return self.extension

  async def get_participation(
    self, _peer_id: uuid.UUID, _name: str
  ) -> ExtensionParticipation:
    return self.participation

  async def list_participations(
    self, _peer_id: uuid.UUID
  ) -> tuple[ExtensionParticipation, ...]:
    return (self.participation,)

  async def set_participation_enabled(
    self, _peer_id: uuid.UUID, _name: str, enabled: bool
  ) -> ExtensionParticipation:
    self.writes.append(enabled)
    if self.write_error is not None:
      raise self.write_error
    self.participation = self.participation.model_copy(update={"enabled": enabled})
    return self.participation


class RuntimeCandidate:
  def __init__(self) -> None:
    self.closed = False
    self.released = False

  async def on_close(self) -> None:
    self.closed = True

  def unpublish(self) -> None:
    pass

  def unbind(self) -> None:
    pass

  def release_runtime(self) -> None:
    self.released = True

  def peer_inbounds(self) -> bool:
    return False


class RuntimeClaim:
  def __init__(self) -> None:
    self.released = False

  def release(self) -> None:
    self.released = True


def _running(
  selection: ExtensionParticipation, candidate: RuntimeCandidate, claim: RuntimeClaim
) -> extension_main.RunningExtension:
  return typing.cast(
    extension_main.RunningExtension,
    SimpleNamespace(
      name=selection.name,
      version=selection.version,
      extension_class=candidate,
      claim=claim,
      stopping=False,
    ),
  )


def _participation(peer_id: uuid.UUID) -> ExtensionParticipation:
  return ExtensionParticipation(
    name="inkcre/race-test",
    peer_id=peer_id,
    distribution="python",
    version="1.2.3",
    enabled=False,
  )


@pytest.mark.parametrize("startup_restore", [False, True])
def test_late_startup_candidate_is_discarded_after_intent_is_disabled(
  async_runner, monkeypatch, startup_restore: bool
):
  peer_id = uuid.uuid4()
  store = ParticipationStore(_participation(peer_id))
  host = ExtensionHost(store=typing.cast(ExtensionStore, store))
  candidate = RuntimeCandidate()
  claim = RuntimeClaim()
  running = _running(store.participation, candidate, claim)
  start_observed_enabled = False

  async def start(_app, _extension, selection):
    nonlocal start_observed_enabled
    start_observed_enabled = store.participation.enabled
    host.running[selection.name] = running
    host._loaded_versions[selection.name] = selection.version
    # Model a concurrent disable while Extension initialization is awaiting.
    store.participation = store.participation.model_copy(update={"enabled": False})
    return running

  typing.cast(typing.Any, host)._start = start

  if startup_restore:
    store.participation = store.participation.model_copy(update={"enabled": True})
    monkeypatch.setattr(
      "app.business.extension.main.PeerManager.get_current_peer_ref",
      lambda: peer_id,
    )
    async_runner.run(host.start_enabled(fastapi.FastAPI()))
  else:
    with pytest.raises(ExtensionStateConflictError, match="changed while startup"):
      async_runner.run(
        host.enable(store.participation.name, peer_id=peer_id, app=fastapi.FastAPI())
      )

  assert start_observed_enabled
  assert not store.participation.enabled
  assert store.participation.version == "1.2.3"
  assert host.running == {}
  assert candidate.closed is True
  assert candidate.released is True
  assert claim.released is True


@pytest.mark.parametrize("reenable", [False, True])
def test_disable_persists_intent_before_cleanup_and_retries_residual_runtime(
  async_runner, monkeypatch, reenable: bool
):
  peer_id = uuid.uuid4()
  store = ParticipationStore(_participation(peer_id).model_copy(update={"enabled": True}))
  name = store.participation.name
  host = ExtensionHost(store=typing.cast(ExtensionStore, store))
  close_error = RuntimeError("close callback failed")
  advertisement_error = RuntimeError("advertisement refresh failed")
  close_fails = True
  advertisement_fails = True
  refresh_count = 0

  class PublishedExtension(RuntimeCandidate):
    published = True

    async def on_close(self) -> None:
      if close_fails:
        assert store.participation.enabled is False
        raise close_error
      await super().on_close()

    def unpublish(self) -> None:
      self.published = False

    def peer_inbounds(self) -> bool:
      return True

  async def refresh(_ttl: int) -> None:
    nonlocal refresh_count
    refresh_count += 1
    assert not candidate.published
    if advertisement_fails:
      raise advertisement_error

  candidate = PublishedExtension()
  claim = RuntimeClaim()
  host.running[name] = _running(store.participation, candidate, claim)
  host.fastapi_app = fastapi.FastAPI()
  monkeypatch.setattr(extension_main.PeerManager, "get_current_peer_ref", lambda: peer_id)
  monkeypatch.setattr(extension_main.AcquiredDistribution, "discover", lambda *_: object())
  monkeypatch.setattr(extension_main.PeerManager, "refresh_self", refresh)
  command = DisableExtensionCommand(action="disable", extension=name)

  with pytest.raises(ExceptionGroup) as caught:
    async_runner.run(host.manage(command, route_to_peer=peer_id))
  assert caught.value.exceptions == (close_error, advertisement_error)
  assert not candidate.published
  assert store.writes == [False]
  assert not claim.released
  status = async_runner.run(host.status(peer_id, name)).extensions[0]
  assert not status.enabled
  assert status.running
  assert status.error == "close callback failed"

  cold = ExtensionHost(store=typing.cast(ExtensionStore, store))

  async def unexpected_start(*_args):
    pytest.fail("cold restore attempted to start a disabled Extension")

  monkeypatch.setattr(cold, "_start", unexpected_start)
  async_runner.run(cold.start_enabled(fastapi.FastAPI()))
  assert not cold.running

  close_fails = False
  advertisement_fails = False
  if reenable:
    fresh_runtime = _running(store.participation, RuntimeCandidate(), RuntimeClaim())

    async def acquire(*_args):
      return None, None

    async def open_runtime(*_args):
      assert candidate.released and claim.released
      host.running[name] = fresh_runtime
      return fresh_runtime

    monkeypatch.setattr(host, "_acquire", acquire)
    monkeypatch.setattr(host, "_start_acquired", open_runtime)
    enable = EnableExtensionCommand(action="enable", extension=name)
    status = async_runner.run(host.manage(enable, route_to_peer=peer_id)).extensions[0]
    assert status.enabled and status.running
    assert host.running[name] is fresh_runtime
    assert store.writes == [False, True]
    return

  status = async_runner.run(host.manage(command, route_to_peer=peer_id)).extensions[0]
  assert not status.enabled
  assert not status.running
  assert status.error is None
  assert candidate.released
  assert claim.released
  assert store.writes == [False]
  assert refresh_count == 2

  async_runner.run(host.manage(command, route_to_peer=peer_id))
  assert store.writes == [False]
  assert refresh_count == 2


def test_disable_retry_does_not_report_a_stale_advertisement_error(
  async_runner, monkeypatch
):
  peer_id = uuid.uuid4()
  store = ParticipationStore(_participation(peer_id).model_copy(update={"enabled": True}))
  name = store.participation.name
  host = ExtensionHost(store=typing.cast(ExtensionStore, store))
  candidate = RuntimeCandidate()
  host.running[name] = _running(store.participation, candidate, RuntimeClaim())

  async def refresh(_ttl: int) -> None:
    raise RuntimeError("advertisement refresh failed")

  monkeypatch.setattr(candidate, "peer_inbounds", lambda: True)
  monkeypatch.setattr(extension_main.PeerManager, "get_current_peer_ref", lambda: peer_id)
  monkeypatch.setattr(extension_main.PeerManager, "refresh_self", refresh)
  command = DisableExtensionCommand(action="disable", extension=name)

  with pytest.raises(RuntimeError, match="advertisement refresh failed"):
    async_runner.run(host.manage(command, route_to_peer=peer_id))
  retry = async_runner.run(host.manage(command, route_to_peer=peer_id)).extensions[0]
  assert not retry.enabled
  assert not retry.running
  assert retry.error is None


def test_disable_leaves_runtime_running_when_intent_write_fails(async_runner):
  peer_id = uuid.uuid4()
  store = ParticipationStore(_participation(peer_id).model_copy(update={"enabled": True}))
  name = store.participation.name
  store.write_error = RuntimeError("intent write failed")
  host = ExtensionHost(store=typing.cast(ExtensionStore, store))
  candidate = RuntimeCandidate()
  running = _running(store.participation, candidate, RuntimeClaim())
  host.running[name] = running

  with pytest.raises(RuntimeError, match="intent write failed"):
    async_runner.run(host.disable(name, peer_id=peer_id))
  assert store.participation.enabled
  assert store.writes == [False]
  assert not candidate.closed
  assert host.running[name] is running
