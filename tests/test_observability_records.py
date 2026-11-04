"""Selected-backend semantics without databases, network or application startup."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fastapi
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy.dialects import postgresql

from inkcre_core import Core
from inkcre_core.libs.obsrv.backends import (
  LokiBackend,
  PostgreSQLBackend,
  ObservabilityUnavailable,
)
from inkcre_core.libs.obsrv.records import (
  ObservationQuery,
  ObservationRecord,
  ObservationRequest,
)
from inkcre_core.libs.obsrv.runtime import ObservationRuntime
from inkcre_core.libs.obsrv.telemetry import emit_event, operation, record_ai_usage


NOW = datetime(2026, 11, 4, 1, 2, 3, 123456, tzinfo=timezone.utc)


def test_record_and_query_reject_unbounded_or_ambiguous_input():
  with pytest.raises(ValueError):
    ObservationRecord(body="x", attributes={"nested": {"content": "x"}})
  with pytest.raises(ValueError):
    ObservationQuery(since=NOW, until=NOW - timedelta(seconds=1))
  with pytest.raises(ValueError):
    ObservationQuery(since=NOW, until=NOW + timedelta(days=32))
  with pytest.raises(ValueError):
    ObservationRequest(
      action="ingest", query=ObservationQuery(since=NOW, until=NOW + timedelta(seconds=1))
    )


def test_loki_roundtrip_encodes_filters_and_does_not_leak_auth(async_runner):
  requests = []
  record = ObservationRecord(
    timestamp=NOW,
    kind="event",
    event_name="job.closed",
    body="job.closed",
    trace_id="job.12",
    attributes={"inkcre.job.id": 12},
  )
  line = None

  def handle(request):
    nonlocal line
    requests.append(request)
    if request.method == "POST":
      stream = json.loads(request.content)["streams"][0]
      assert stream["stream"] == {"service_name": "inkcre", "deployment_id": "deployment"}
      assert stream["values"][0][0] == "1793754123123456000"
      line = stream["values"][0][1]
      return httpx.Response(204)
    assert line is not None
    query = request.url.params["query"]
    assert 'job = "12" or trace = "job.12"' in query
    assert "severity >= 9" in query
    assert 'kind = "event"' in query
    # Text is escaped as data in both regexp and LogQL string layers.
    assert "\\\\." in query
    assert request.url.params["limit"] == "2"
    return httpx.Response(
      200,
      json={
        "status": "success",
        "data": {
          "resultType": "streams",
          "result": [
            {"values": [["1793754123123456000", line], ["1793754123123456000", line]]}
          ],
        },
      },
    )

  async def scenario():
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
      backend = LokiBackend(
        url="https://logs.example", deployment_id="deployment", client=client
      )
      await backend.write([record])
      page = await backend.query(
        ObservationQuery(
          since=NOW - timedelta(seconds=1),
          until=NOW + timedelta(seconds=1),
          limit=1,
          kind="event",
          severity_min=9,
          job_id=12,
          text='a.b\n" | logfmt',
        )
      )
      assert page.records == [record]
      assert page.truncated and page.backend == "loki"
      await backend.aclose()
      assert not client.is_closed  # Borrowed client remains owned by its caller.

  async_runner.run(scenario())


def test_external_failure_is_not_empty_or_a_database_fallback(async_runner):
  async def scenario():
    async with httpx.AsyncClient(
      transport=httpx.MockTransport(lambda _: httpx.Response(500, text="private token"))
    ) as client:
      backend = LokiBackend(
        url="https://logs.example", deployment_id="deployment", client=client
      )
      runtime = ObservationRuntime(backend, selected="loki", peer_id="peer")
      runtime.start()
      assert runtime.emit(
        ObservationRecord(
          body="secret content", attributes={"query": "private", "inkcre.job.id": 12}
        )
      )
      with pytest.raises(ObservabilityUnavailable, match="query failed") as error:
        await runtime.query(ObservationQuery(since=NOW, until=NOW + timedelta(seconds=1)))
      assert "private" not in str(error.value)
      await runtime.aclose()
      assert runtime.dropped == 1
      assert runtime.status().backend == "loki"

  async_runner.run(scenario())


def test_metadata_and_instance_lifetimes_do_not_need_otlp(async_runner, monkeypatch):
  from inkcre_core.libs.obsrv import telemetry

  monkeypatch.setattr(telemetry, "_current_runtime", lambda: None)

  async def scenario():
    first = Core("postgresql+psycopg://test:test@127.0.0.1:1/test")
    second = Core("postgresql+psycopg://test:test@127.0.0.1:1/test")
    backends = [MagicMock(write=AsyncMock(), aclose=AsyncMock()) for _ in range(2)]
    for core, backend in zip((first, second), backends):
      core.observability = ObservationRuntime(
        backend, selected="loki", peer_id=str(core.options.peer_id)
      )
      core.observability.start()
    assert first.observability is not None
    assert second.observability is not None
    with first.bind():
      emit_event("job.started", {"inkcre.job.id": 12})
      with operation("job.execute", attributes={"inkcre.job.id": 12}):
        record_ai_usage("chat", 0, None)
      first.observability.emit(
        ObservationRecord(body="secret prompt", attributes={"query": "private"})
      )
      with second.bind():
        emit_event("job.started", {"inkcre.job.id": 24})
    await first.aclose()
    first_records = backends[0].write.call_args.args[0]
    assert [record.event_name for record in first_records] == [
      "job.started",
      "ai.usage",
      "job.execute",
      None,
    ]
    assert first_records[1].attributes["input_tokens"] == 0
    assert "output_tokens" not in first_records[1].attributes
    assert first_records[2].attributes["duration_ms"] >= 0
    assert first_records[3].body == "application.log"
    assert "query" not in first_records[3].attributes
    assert second.observability.status().recording
    await second.aclose()
    assert backends[1].write.call_args.args[0][0].attributes["inkcre.job.id"] == 24

  async_runner.run(scenario())


def test_postgresql_preserves_legacy_job_association_and_filters_before_limit(async_runner):
  session = MagicMock()
  session.execute = AsyncMock(return_value=MagicMock())
  session.execute.return_value.scalars.return_value.all.return_value = [
    SimpleNamespace(
      id=8,
      timestamp=NOW,
      severity_number=17,
      severity_text="ERROR",
      body="historical body",
      trace_id="job.12",
      span_id=None,
      attributes={"legacy": {"nested": True}},
    )
  ]
  sessions = MagicMock()
  sessions.return_value.__aenter__.return_value = session

  async def scenario():
    backend = PostgreSQLBackend(sessions)
    page = await backend.query(
      ObservationQuery(
        since=NOW - timedelta(seconds=1),
        until=NOW + timedelta(seconds=1),
        job_id=12,
        text="100%",
        severity_min=13,
        limit=1,
      )
    )
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "job.12" in compiled.params.values()
    assert "%100\\%%" in compiled.params.values()
    assert str(compiled).index("WHERE") < str(compiled).index("LIMIT")
    assert page.records[0].id == "pg.8"
    assert page.records[0].kind == "log" and page.records[0].event_name is None
    assert page.records[0].attributes == {"legacy": {"nested": True}}

  async_runner.run(scenario())


def test_route_refuses_stale_backend_and_hides_invalid_body(async_runner):
  from app.core import CoreMiddleware
  from app.routes.observability import ROUTER

  core = Core("postgresql+psycopg://test:test@127.0.0.1:1/test")
  core.observability = ObservationRuntime(None, selected="postgresql", peer_id="peer")
  app = fastapi.FastAPI()
  app.add_middleware(CoreMiddleware, core=core)
  app.include_router(ROUTER)
  with TestClient(app) as client:
    response = client.post(
      "/observability", json={"action": "ingest", "records": [], "expected_backend": "loki"}
    )
    assert response.status_code == 409
    response = client.post("/observability", json={"action": "bad-secret"})
    assert response.status_code == 422 and "bad-secret" not in response.text
    response = client.post("/observability", content=b"x" * (256 * 1024 + 1))
    assert response.status_code == 413
  async_runner.run(core.aclose())


def test_failed_metadata_and_malformed_logging_keep_business_semantics(
  async_runner, monkeypatch
):
  import logging
  from inkcre_core.libs.obsrv import telemetry
  from inkcre_core.libs.obsrv.log_record import ENABLE_LOG_BACKEND
  from inkcre_core.libs.obsrv.runtime import ObservationLogHandler

  monkeypatch.setattr(telemetry, "_current_runtime", lambda: None)

  async def scenario():
    core = Core("postgresql+psycopg://test:test@127.0.0.1:1/test")
    backend = MagicMock(write=AsyncMock(), aclose=AsyncMock())
    runtime = ObservationRuntime(backend, selected="postgresql", peer_id="peer")
    core.observability = runtime
    runtime.start()
    with core.bind():
      emit_event("job.closed", {"inkcre.job.id": 12, "inkcre.job.status": "failed"})
      with pytest.raises(RuntimeError, match="business error"):
        with operation("test.operation"):
          raise RuntimeError("business error")
      token = ENABLE_LOG_BACKEND.set(True)
      try:
        ObservationLogHandler().emit(
          logging.LogRecord("inkcre", 20, "", 1, "%d", ("invalid",), None)
        )
        ObservationLogHandler().emit(
          logging.LogRecord("inkcre", 20, "", 1, "x" * 8193, (), None)
        )
        nested = logging.LogRecord("inkcre", 20, "", 1, "fixed", (), None)
        nested.extra_payload = {"unexpected": "nested"}
        ObservationLogHandler().emit(nested)
      finally:
        ENABLE_LOG_BACKEND.reset(token)
    await core.aclose()
    records = backend.write.call_args.args[0]
    assert len(records) == 2
    assert all(record.severity_number == 17 for record in records)
    assert runtime.dropped == 3

  async_runner.run(scenario())


def test_public_selector_is_authority_and_failed_external_config_never_defaults(
  async_runner, monkeypatch
):
  from app import observability
  from inkcre_core.schemas.observability import ObservabilityBackendConfig

  monkeypatch.setattr(observability.settings.obsrv, "telemetry_enabled", False)
  monkeypatch.setattr(observability.settings.obsrv, "loki_url", None)

  async def scenario():
    core = Core("postgresql+psycopg://test:test@127.0.0.1:1/test")
    with core.bind():
      get = AsyncMock(return_value=None)
      monkeypatch.setattr(observability.DeploymentConfigService, "get", get)
      await observability.initialize_telemetry()
      assert core.observability is not None
      assert core.observability.selected == "postgresql"
      assert isinstance(core.observability.backend, PostgreSQLBackend)
      get.assert_awaited_once_with("inkcre.observability.backend")
      get.side_effect = [ObservabilityBackendConfig(backend="loki"), None]
      await observability.initialize_telemetry()
      assert core.observability.selected == "loki"
      assert core.observability.backend is None
      assert not core.observability.status().recording
      get.side_effect = RuntimeError("secret configuration response")
      await observability.initialize_telemetry()
      assert core.observability.backend is None
      assert "secret" not in (core.observability.error or "")
    await core.aclose()

  async_runner.run(scenario())


def test_optional_otlp_connection_belongs_to_bound_core(async_runner):
  from inkcre_core.libs.obsrv.telemetry import _Runtime, get_export_connection

  first = Core("postgresql+psycopg://test:test@127.0.0.1:1/test")
  second = Core("postgresql+psycopg://test:test@127.0.0.1:1/test")
  first.telemetry = _Runtime(connections={"logs": ("https://first.invalid", {}, 1)})
  second.telemetry = _Runtime(connections={"logs": ("https://second.invalid", {}, 1)})

  async def scenario():
    with first.bind():
      connection = get_export_connection("logs")
      assert connection is not None and connection[0] == "https://first.invalid"
      with second.bind():
        connection = get_export_connection("logs")
        assert connection is not None and connection[0] == "https://second.invalid"
    await first.aclose()
    assert first.telemetry is None
    assert second.telemetry is not None
    await second.aclose()

  async_runner.run(scenario())


def test_capability_bounds_slow_body_and_concurrent_requests(async_runner, monkeypatch):
  import asyncio
  from app.routes import observability

  core = Core("postgresql+psycopg://test:test@127.0.0.1:1/test")
  runtime = ObservationRuntime(None, selected="postgresql", peer_id="peer")
  core.observability = runtime
  original_timeout = asyncio.timeout
  monkeypatch.setattr(
    observability, "asyncio", SimpleNamespace(timeout=lambda _: original_timeout(0.001))
  )

  async def receive():
    await asyncio.Event().wait()
    return {"type": "http.request", "body": b"", "more_body": False}

  async def scenario():
    with core.bind():
      request = fastapi.Request({"type": "http"}, receive=receive)
      runtime.requests_in_flight = 4
      with pytest.raises(fastapi.HTTPException) as capacity:
        await observability.observability(request)
      assert capacity.value.status_code == 429
      runtime.requests_in_flight = 0
      with pytest.raises(fastapi.HTTPException) as timeout:
        await observability.observability(request)
      assert timeout.value.status_code == 408
      assert runtime.requests_in_flight == 0
    await core.aclose()

  async_runner.run(scenario())
