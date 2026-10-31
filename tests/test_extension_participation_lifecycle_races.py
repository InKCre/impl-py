"""Intent changes during Extension startup must discard stale runtime candidates."""

import uuid
import typing

import fastapi
import pytest

from app.business.extension.errors import ExtensionStateConflictError
from app.business.extension.main import ActiveExtensionEffects, ExtensionHost
from app.business.extension.state import (
  ExtensionParticipation,
  ExtensionStore,
  InstalledExtension,
)


class ParticipationStore:
  def __init__(self, participation: ExtensionParticipation) -> None:
    self.participation = participation
    self.extension = InstalledExtension(name=participation.name, nickname="Race test")

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
  running = type(
    "RunningCandidate",
    (),
    {
      "name": store.participation.name,
      "version": "1.2.3",
      "extension_class": candidate,
      "claim": claim,
      "effects": ActiveExtensionEffects(
        typing.cast(typing.Any, claim), typing.cast(typing.Any, candidate)
      ),
    },
  )()
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
