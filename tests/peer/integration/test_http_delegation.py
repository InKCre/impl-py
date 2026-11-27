"""Peer delegation over real PostgreSQL discovery and local HTTP connections."""

import asyncio
from datetime import timedelta
import os
import random
import uuid

from aiohttp import web
import httpx
import jwt
import pytest
import sqlalchemy as sa
from sqlmodel import col

from inkcre_core import Core, CoreOptions
from inkcre_core.business.peer import PeerManager, PeerOutcomeUnknown
from inkcre_core.schemas.ai import JSONValue
from inkcre_core.schemas.peer import PeerModel
from tests.database import TestSession


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)

SECRET = "peer-delegation-test-secret-at-least-32-bytes"  # noqa: S105


class CandidateOrder(random.Random):
  """Fix only the existing random ordering seam; discover candidates normally."""

  def shuffle(self, x):
    x.sort(key=lambda candidate: candidate.peer.id)


@pytest.mark.parametrize("outcome", ["not-executed", "disconnected-after-execution"])
def test_delegation_retries_only_after_proven_non_execution(outcome, monkeypatch):
  monkeypatch.setattr(PeerManager, "_random", CandidateOrder())

  async def scenario():
    capability = f"tests.peer.http.{uuid.uuid4().hex}"
    peer_ids = sorted((uuid.uuid4(), uuid.uuid4()))
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
      received.append(request.match_info["peer"])
      if request.match_info["peer"] == "first":
        if outcome == "not-executed":
          return web.json_response(
            {"reason": "not ready"},
            status=503,
            headers={"InkCre-Peer-Execution": "not-executed"},
          )
        effects.append("first")
        # The server received and acted on the body, but no response reached the caller.
        assert request.transport is not None
        request.transport.close()
        return web.Response()
      effects.append("fallback")
      return web.json_response({"accepted_by": "fallback"})

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
        for peer_id, path in zip(peer_ids, ("first", "fallback"), strict=True):
          session.add(
            PeerModel(
              id=peer_id,
              name=f"HTTP {path}",
              lease_expires_at=now + timedelta(minutes=1),
              capabilities=[
                {
                  "id": capability,
                  "inbound": {
                    "protocol": "core.peer.protocol.http.v1",
                    "parameters": {
                      "method": "POST",
                      "url": f"http://127.0.0.1:{port}/{path}",
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
          assert result["status"] == 200 and result["body"] == {"accepted_by": "fallback"}
          assert received == ["first", "fallback"]
          assert effects == ["fallback"]
        else:
          with pytest.raises(
            PeerOutcomeUnknown, match="dispatch outcome is unknown"
          ) as failed:
            await PeerManager.delegate(capability, payload)
          assert isinstance(failed.value.__cause__, httpx.RemoteProtocolError)
          assert received == ["first"]
          assert effects == ["first"]
    finally:
      await core.aclose()
      await runner.cleanup()
      with TestSession() as session:
        session.connection().execute(
          sa.delete(PeerModel).where(col(PeerModel.id).in_(peer_ids))
        )
        session.commit()

  asyncio.run(scenario())
