"""Real PostgreSQL proof for Peer snapshots, database-time lease and discovery."""

from dataclasses import replace
from inkcre_core import current_core
import datetime
import os
import time
import uuid

import pytest
import sqlalchemy

from inkcre_core.business.peer import PeerHTTPInbound, PeerManager
from tests.database import TestSession
from inkcre_core.schemas.peer import PEER_HTTP_PROTOCOL, PeerModel
from app.version import APPLICATION_VERSION


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)

CAPABILITY = "core.peer.integration.v1"


def test_real_snapshot_database_lease_and_candidate_filtering(monkeypatch, async_runner):
  original_options = current_core().options
  original_inbounds = current_core().inbounds
  original_outbounds = current_core().outbounds
  local = uuid.uuid4()
  live = uuid.uuid4()
  expired = uuid.uuid4()
  malformed = uuid.uuid4()
  peer_ids = (local, live, expired, malformed)
  try:
    monkeypatch.setattr(current_core(), "inbounds", {})
    monkeypatch.setattr(current_core(), "outbounds", {})
    monkeypatch.setattr(
      current_core(),
      "options",
      replace(current_core().options, peer_id=local, peer_name="integration-local"),
    )

    async_runner.run(PeerManager.register_self())
    PeerManager.setup_builtin_outbounds()
    PeerManager.register_inbound(PeerHTTPInbound(CAPABILITY, "POST", "/integration-action"))
    with TestSession() as db:
      local_row = db.get(PeerModel, local)
      assert local_row is not None
      assert local_row.application_version == APPLICATION_VERSION
      local_row.name = "Human name"
      local_row.application_version = "0.0.0"
      local_row.config = {"http_public_base_url": "https://local.example/root/"}
      db.add(local_row)
      now = (
        db.connection()
        .execute(sqlalchemy.select(sqlalchemy.func.statement_timestamp()))
        .scalar_one()
      )
      advertisement = {
        "id": CAPABILITY,
        "inbound": {
          "protocol": PEER_HTTP_PROTOCOL,
          "parameters": {
            "method": "POST",
            "url": "https://remote.example/integration-action",
          },
        },
      }
      db.add(
        PeerModel(
          id=live,
          name="live",
          capabilities=[advertisement],
          lease_expires_at=now + datetime.timedelta(minutes=1),
        )
      )
      db.add(
        PeerModel(
          id=expired,
          name="expired",
          capabilities=[advertisement],
          lease_expires_at=now - datetime.timedelta(seconds=1),
        )
      )
      db.add(
        PeerModel(
          id=malformed,
          name="malformed",
          capabilities=[{"id": CAPABILITY}],
          lease_expires_at=now + datetime.timedelta(minutes=1),
        )
      )
      db.commit()

    registered_again = async_runner.run(PeerManager.register_self())
    assert registered_again.name == "Human name"
    assert registered_again.application_version == APPLICATION_VERSION

    published = async_runner.run(PeerManager.publish_self())
    assert published.capabilities == [
      {
        "id": CAPABILITY,
        "inbound": {
          "protocol": PEER_HTTP_PROTOCOL,
          "parameters": {
            "method": "POST",
            "url": "https://local.example/root/integration-action",
          },
        },
      }
    ]
    expiry = async_runner.run(PeerManager.renew_self_lease(45))
    with TestSession() as db:
      remaining = (
        db.connection()
        .execute(
          sqlalchemy.select(
            sqlalchemy.func.extract(
              "epoch",
              expiry - sqlalchemy.func.statement_timestamp(),
            )
          )
        )
        .scalar_one()
      )
    assert 40 < float(remaining) <= 45

    candidates = async_runner.run(PeerManager._candidates(CAPABILITY, None))
    assert [candidate.peer.id for candidate in candidates] == [live]
    assert async_runner.run(PeerManager._candidates(CAPABILITY, live))[0].peer.id == live
    assert async_runner.run(PeerManager._candidates(CAPABILITY, expired)) == ()

    async_runner.run(PeerManager.clear_self_lease())
    cleared = async_runner.run(PeerManager.get_async(local))
    assert cleared is not None
    assert cleared.lease_expires_at is None
  finally:
    monkeypatch.setattr(current_core(), "inbounds", original_inbounds)
    monkeypatch.setattr(current_core(), "outbounds", original_outbounds)
    monkeypatch.setattr(current_core(), "options", original_options)
    with TestSession() as db:
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.peers WHERE id = ANY(:ids)"),
        {"ids": list(peer_ids)},
      )
      db.commit()


def test_failed_cold_restore_preserves_intent_and_reports_not_ready(monkeypatch):
  """A real Registry failure must not publish an executable Peer lease."""
  from fastapi.testclient import TestClient

  import run
  from inkcre_core.schemas.extension import ExtensionModel, ExtensionPeerParticipationModel

  from app.core import create_core

  peer = uuid.uuid4()
  name = f"issue121/missing-{peer.hex}"
  # This lifespan closes its owned Core; do not close other tests' service instance.
  service_core = create_core()
  service_core.options = replace(service_core.options, peer_id=peer)
  original_core = run.CORE
  monkeypatch.setattr(run, "CORE", service_core)
  monkeypatch.setattr(run.api_app, "middleware_stack", None)
  for middleware in run.api_app.user_middleware:
    if middleware.kwargs.get("core") is original_core:
      monkeypatch.setitem(middleware.kwargs, "core", service_core)
  monkeypatch.setattr(run, "SKIP_EXTENSION_START", False)
  with TestSession() as db:
    db.add(
      PeerModel(
        id=peer,
        name="failed-restore-test",
        config={"extension_registry_url": "http://127.0.0.1:1"},
      )
    )
    db.flush()
    db.add(ExtensionModel(name=name))
    db.add(
      ExtensionPeerParticipationModel(
        name=name,
        peer_id=peer,
        distribution="python",
        version="0.0.0",
        enabled=True,
      )
    )
    db.commit()
  try:
    with TestClient(run.api_app) as client:
      deadline = time.monotonic() + 10
      while True:
        response = client.get("/readyz")
        runtime = response.json()["runtime"]
        if runtime["phase"] == "failed":
          break
        assert time.monotonic() < deadline, runtime
        time.sleep(0.02)
      assert response.status_code == 503
      assert runtime == {
        "phase": "failed",
        "reason": "runtime_bootstrap_failed",
        "step": "extensions",
      }
      assert client.get("/livez").status_code == 200
      with TestSession() as db:
        participation = db.get(ExtensionPeerParticipationModel, (name, peer))
        assert participation is not None and participation.enabled
        registered = db.get(PeerModel, peer)
        assert registered is not None and registered.lease_expires_at is None
  finally:
    with TestSession() as db:
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.extensions WHERE name = :name"),
        {"name": name},
      )
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.peers WHERE id = :peer"), {"peer": peer}
      )
      db.commit()
