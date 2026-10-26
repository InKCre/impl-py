import uuid
import typing

import pytest

from app.business.extension.main import ExtensionHost
from app.business.extension.state import ExtensionStore, InstalledExtension
from app.schemas.extension.main import ExtensionPeerStatus, SelectExtensionCommand


def test_shared_protocol_preserves_a_distribution_unknown_to_python():
  command = SelectExtensionCommand(
    action="select", extension="inkcre/capture", distribution="kotlin_apk", version="0.1.0"
  )
  status = ExtensionPeerStatus(
    peer_id=uuid.uuid4(), supported_distributions=(command.distribution,), extensions=()
  )
  assert status.supported_distributions == ("kotlin_apk",)


class EmptyParticipationStore:
  def __init__(self, extensions: tuple[InstalledExtension, ...] = ()) -> None:
    self.extensions = extensions

  async def list(self) -> tuple[InstalledExtension, ...]:
    return self.extensions

  async def list_participations(self, _peer_id: uuid.UUID) -> tuple[()]:
    return ()


@pytest.mark.parametrize(
  ("extensions", "expected_rows"),
  [
    ((), 0),
    ((InstalledExtension(name="inkcre/mail", nickname="Mail"),), 1),
  ],
)
def test_status_reports_peer_capabilities_before_and_after_shared_install(
  async_runner,
  extensions: tuple[InstalledExtension, ...],
  expected_rows: int,
):
  peer_id = uuid.UUID("00000000-0000-4000-8000-000000000117")
  host = ExtensionHost(
    store=typing.cast(ExtensionStore, EmptyParticipationStore(extensions))
  )

  status = async_runner.run(host.status(peer_id, "inkcre/mail"))

  assert status.peer_id == peer_id
  assert status.supported_distributions == ("python",)
  assert len(status.extensions) == expected_rows
  if expected_rows:
    row = status.extensions[0]
    assert row.name == "inkcre/mail"
    assert row.distribution is None
    assert row.version is None
    assert row.enabled is False
    assert row.running is False
