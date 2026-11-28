"""Peer delegation over real PostgreSQL discovery and local HTTP connections."""

import asyncio
from datetime import timedelta
import os
import socket
import uuid

from aiohttp import web
import httpx
import jwt
import pytest
import sqlalchemy as sa
from sqlmodel import col

from inkcre_core import Core, CoreOptions
from inkcre_core.business.peer import (
  PeerHTTPOutbound,
  PeerManager,
  PeerOutcomeUnknown,
  PeerRequestNotExecuted,
)
from inkcre_core.schemas.ai import JSONValue
from inkcre_core.schemas.peer import PeerModel
from tests.database import TestSession


SECRET = "peer-delegation-test-secret-at-least-32-bytes"  # noqa: S105


def test_refused_connection_reports_that_the_request_was_not_executed(monkeypatch):
  monkeypatch.setenv("no_proxy", "127.0.0.1")
  peer = PeerModel(id=uuid.uuid4(), name="offline")
  # Reserve the port without listening so no other process can accept the request.
  with socket.socket() as reserved:
    reserved.bind(("127.0.0.1", 0))
    host, port = reserved.getsockname()
    outbound = PeerHTTPOutbound(
      peer, {"method": "POST", "url": f"http://{host}:{port}/work"}
    )
    with pytest.raises(PeerRequestNotExecuted) as failed:
      asyncio.run(outbound.execute({"body": {"operation": "append"}}))
  assert isinstance(failed.value.__cause__, httpx.ConnectError)


@pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)
@pytest.mark.parametrize(
  "outcome", ["not-executed", "responded-after-execution", "disconnected-after-execution"]
)
def test_delegation_retries_only_after_proven_non_execution(outcome):
  async def scenario():
    capability = f"tests.peer.http.{uuid.uuid4().hex}"
    peer_ids = (uuid.uuid4(), uuid.uuid4())
    received = []
    effects = []

    async def endpoint(request):
      claims = jwt.decode(
        request.headers["Authorization"].removeprefix("Bearer "),
        SECRET,
        algorithms=["HS256"],
        audience="inkcre-api",
        issuer="inkcre-peer",
        options={"require": ["iat", "exp", "role"]},
      )
      assert claims["role"] == "authenticated"
      assert request.method == "POST"
      assert request.query.getall("label") == ["one", "two"]
      body = await request.json()
      assert body == {"operation": "append", "content": "one mutation"}
      peer = request.match_info["peer"]
      received.append(peer)
      if len(received) == 1:
        if outcome == "not-executed":
          return web.json_response(
            {"reason": "not ready"},
            status=503,
            headers={"InkCre-Peer-Execution": "not-executed"},
          )
        effects.append(peer)
        if outcome == "responded-after-execution":
          return web.json_response({"stored_by": peer}, status=503)
        # The server received and acted on the body, but no response reached the caller.
        assert request.transport is not None
        request.transport.close()
        return web.Response()
      effects.append(peer)
      return web.json_response({"accepted_by": peer})

    app = web.Application()
    app.router.add_post("/{peer}", endpoint)
    runner = web.AppRunner(app)
    core = Core(
      os.environ["INKCRE_TEST_DATABASE_URL"],
      options=CoreOptions(jwt_secret=SECRET, peer_http_timeout_seconds=5),
    )
    try:
      await runner.setup()
      site = web.TCPSite(runner, "127.0.0.1", 0)
      await site.start()
      port = runner.addresses[0][1]
      with TestSession() as session:
        now = (
          session.connection()
          .execute(sa.select(sa.func.statement_timestamp()))
          .scalar_one()
        )
        for peer_id in peer_ids:
          session.add(
            PeerModel(
              id=peer_id,
              name=f"HTTP {peer_id}",
              lease_expires_at=now + timedelta(minutes=1),
              capabilities=[
                {
                  "id": capability,
                  "inbound": {
                    "protocol": "core.peer.protocol.http.v1",
                    "parameters": {
                      "method": "POST",
                      "url": f"http://127.0.0.1:{port}/{peer_id}",
                    },
                  },
                }
              ],
            )
          )
        session.commit()

      with core.bind():
        PeerManager.setup_builtin_outbounds()
        payload: JSONValue = {
          "query": {"label": ["one", "two"]},
          "body": {"operation": "append", "content": "one mutation"},
        }
        if outcome == "not-executed":
          result = await PeerManager.delegate(capability, payload)
          assert isinstance(result, dict)
          assert len(received) == 2 and set(received) == {str(peer) for peer in peer_ids}
          assert result["status"] == 200 and result["body"] == {"accepted_by": received[1]}
          assert effects == [received[1]]
        elif outcome == "responded-after-execution":
          result = await PeerManager.delegate(capability, payload)
          assert len(received) == 1 and effects == received
          assert isinstance(result, dict)
          assert result["status"] == 503 and result["body"] == {"stored_by": received[0]}
        else:
          with pytest.raises(
            PeerOutcomeUnknown, match="dispatch outcome is unknown"
          ) as failed:
            await PeerManager.delegate(capability, payload)
          assert isinstance(failed.value.__cause__, httpx.RemoteProtocolError)
          assert len(received) == 1 and effects == received
    finally:
      await core.aclose()
      await runner.cleanup()
      with TestSession() as session:
        session.connection().execute(
          sa.delete(PeerModel).where(col(PeerModel.id).in_(peer_ids))
        )
        session.commit()

  asyncio.run(scenario())
