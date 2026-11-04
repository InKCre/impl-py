"""SDK bindings isolate concurrent deployments and preserve borrowed resources."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from inkcre_core import Core, CoreOptions, current_core
from inkcre_core.business.peer import PeerManager


def test_concurrent_instances_keep_their_identity_and_restore_nested_scope(async_runner):
  first = Core("postgresql+psycopg://one:one@127.0.0.1:1/one")
  second = Core("postgresql+psycopg://two:two@127.0.0.1:1/two")
  caller = current_core()

  async def work(core, other):
    with core.bind():
      assert PeerManager.get_current_peer_ref() == core.options.peer_id
      with other.bind():
        assert current_core() is other
      await asyncio.sleep(0)
      assert current_core() is core
      assert core.sources is not other.sources
      assert core.active_jobs is not other.active_jobs
      return PeerManager.get_current_peer_ref()

  async def run():
    try:
      result = await asyncio.gather(work(first, second), work(second, first))
      assert result == [first.options.peer_id, second.options.peer_id]
    finally:
      await first.aclose()
      await second.aclose()

  async_runner.run(run())
  assert current_core() is caller
  with pytest.raises(RuntimeError, match="closed"):
    with first.bind():
      pass


def test_borrowed_engine_is_not_disposed_and_options_are_explicit(async_runner):
  engine = create_async_engine("postgresql+psycopg://borrowed:x@127.0.0.1:1/test")
  options = CoreOptions(peer_name="external-consumer")
  core = Core(engine, options=options)
  with patch.object(type(engine), "dispose", new_callable=AsyncMock) as dispose:
    async_runner.run(core.aclose())
    async_runner.run(core.aclose())
    dispose.assert_not_awaited()
  async_runner.run(engine.dispose())
  assert core.options == options
  assert replace(options, peer_name="another") != core.options


def test_owned_engine_is_disposed_once(async_runner):
  core = Core("postgresql+psycopg://owned:x@127.0.0.1:1/test")
  with patch.object(type(core.engine), "dispose", new_callable=AsyncMock) as dispose:
    async_runner.run(core.aclose())
    async_runner.run(core.aclose())
    dispose.assert_awaited_once()


def test_legacy_imports_share_model_and_service_identity():
  from app.schemas.info_base.block import BlockModel as LegacyBlock
  from inkcre_core.schemas.info_base.block import BlockModel
  from app.business.info_base.main import InfoBaseManager as LegacyManager
  from inkcre_core.business.info_base.main import InfoBaseManager

  assert LegacyBlock is BlockModel
  assert LegacyManager is InfoBaseManager


def test_closing_one_core_drains_only_its_pending_work(async_runner):
  first = Core("postgresql+psycopg://one:x@127.0.0.1:1/one")
  second = Core("postgresql+psycopg://two:x@127.0.0.1:1/two")

  async def wait():
    await asyncio.Event().wait()
    return True

  async def run():
    with first.bind():
      a = asyncio.create_task(wait())
      first.pending_jobs[1] = a
    with second.bind():
      b = asyncio.create_task(wait())
      second.pending_jobs[2] = b
    await first.aclose()
    assert a.cancelled()
    assert not b.done()
    await second.aclose()
    assert b.cancelled()

  async_runner.run(run())


def test_asgi_binding_covers_lifespan_and_requests(async_runner):
  from app.core import CoreMiddleware

  core = Core("postgresql+psycopg://test:x@127.0.0.1:1/test")

  async def application(scope, receive, send):
    await asyncio.sleep(0)
    assert current_core() is core

  async def receive():
    raise AssertionError("This probe does not receive HTTP data")

  async def send(message):
    raise AssertionError("This probe does not send HTTP data")

  async def run():
    middleware = CoreMiddleware(application, core)
    await middleware({"type": "lifespan"}, receive, send)
    await middleware({"type": "http"}, receive, send)
    await core.aclose()

  async_runner.run(run())


def test_job_execution_keeps_logging_when_woken_by_a_quiet_poll(async_runner, monkeypatch):
  from inkcre_core.business.job import JobManager
  from inkcre_core.libs.obsrv.log_record import ENABLE_LOG_BACKEND, TRACE_ID

  async def execute(job_id):
    assert job_id == 42
    assert ENABLE_LOG_BACKEND.get()
    assert TRACE_ID.get() == "job.42"
    return True

  monkeypatch.setattr(JobManager, "run", execute)

  async def run():
    token = ENABLE_LOG_BACKEND.set(False)
    try:
      assert await JobManager._run_traced(42)
      assert not ENABLE_LOG_BACKEND.get()
    finally:
      ENABLE_LOG_BACKEND.reset(token)

  async_runner.run(run())
