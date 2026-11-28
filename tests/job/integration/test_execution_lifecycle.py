"""Durable execution, stop intent and scheduled occurrences on real PostgreSQL."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import uuid

import pydantic
import pytest
import sqlalchemy as sa

from inkcre_core import Core
from inkcre_core.libs.obsrv.log_record import ENABLE_LOG_BACKEND, TRACE_ID
from inkcre_core.business.cron import CronManager
from inkcre_core.business.job import JobHandler, JobManager
from inkcre_core.persistence.cron.repository import CronRepository
from inkcre_core.schemas.cron import CronForm
from inkcre_core.schemas.job import JobStatus, JobTypeModel
from tests.database import TestSession


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)


class Parameters(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")


@pytest.fixture
def job_type(monkeypatch):
  identifier = f"tests.execution.{uuid.uuid4().hex}"
  monkeypatch.setattr(JobManager, "_handlers", dict(JobManager._handlers))
  with TestSession() as session:
    session.add(
      JobTypeModel(
        id=identifier,
        description="Disposable execution contract",
        parameters_schema=Parameters.model_json_schema(),
        default_timeout_seconds=60,
      )
    )
    session.commit()
  try:
    yield identifier
  finally:
    with TestSession() as session:
      for table, column in (("crons", "job_type"), ("jobs", "type"), ("job_types", "id")):
        session.connection().execute(
          sa.text(f"DELETE FROM inkcre.{table} WHERE {column} = :kind"),
          {"kind": identifier},
        )
      session.commit()


@pytest.fixture
def execution_runtime():
  core = Core(os.environ["INKCRE_TEST_DATABASE_URL"])
  with core.bind(), asyncio.Runner() as runner:
    JobManager.start()
    try:
      yield runner
    finally:
      runner.run(core.aclose())


def test_abort_is_durable_but_running_work_closes_after_cleanup(
  job_type, execution_runtime
):
  async def scenario():
    entered = asyncio.get_running_loop().create_future()
    cleaning = asyncio.Event()
    release = asyncio.Event()

    class Handler(
      JobHandler,
      job_type=job_type,
      description="Wait for stop and cleanup",
      parameters_model=Parameters,
      default_timeout_seconds=60,
    ):
      @classmethod
      async def can_handle(cls, parameters):
        return True

      @classmethod
      async def handle(cls, job, parameters):
        entered.set_result(
          (asyncio.current_task(), ENABLE_LOG_BACKEND.get(), TRACE_ID.get())
        )
        try:
          await asyncio.Event().wait()
        finally:
          cleaning.set()
          await release.wait()
          job.state = {"resources_released": True}

    observer = Core(os.environ["INKCRE_TEST_DATABASE_URL"])
    try:
      job = await JobManager.create(job_type, {})
      assert job.id is not None
      quiet_poll = ENABLE_LOG_BACKEND.set(False)
      try:
        await JobManager.check()
        assert not ENABLE_LOG_BACKEND.get()
      finally:
        ENABLE_LOG_BACKEND.reset(quiet_poll)
      async with asyncio.timeout(10):
        executor, logging_enabled, trace_id = await entered
        assert logging_enabled and trace_id == f"job.{job.id}"
        with observer.bind():
          requested = await JobManager.abort(job.id)
          assert requested is not None
          assert requested.status == JobStatus.RUNNING and requested.abort_requested
          assert requested.closed_at is None
        await JobManager.check_abort_requests()
        await cleaning.wait()
        with observer.bind():
          repeated = await JobManager.abort(job.id)
          assert repeated is not None and repeated.status == JobStatus.RUNNING
        await JobManager.check_abort_requests()
        assert executor is not None and not executor.done()
        release.set()
        await executor
      with observer.bind():
        finished = await JobManager.get(job.id)
        assert finished is not None and finished.status == JobStatus.ABORTED
        assert finished.state == {"resources_released": True}
        assert finished.closed_at is not None
        unchanged = await JobManager.abort(job.id)
        assert unchanged is not None and unchanged.closed_at == finished.closed_at
        pending = await JobManager.create(job_type, {})
        assert pending.id is not None
        stopped = await JobManager.abort(pending.id)
        assert stopped is not None and stopped.status == JobStatus.ABORTED
        assert stopped.started_at is None and stopped.closed_at is not None
    finally:
      release.set()
      await observer.aclose()

  execution_runtime.run(scenario())


def test_cron_serializes_occurrences_and_waits_for_outstanding_job(
  job_type, execution_runtime, monkeypatch
):
  minute = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)

  async def database_now(_repository):
    return minute

  monkeypatch.setattr(CronRepository, "database_now", database_now)

  async def scenario():
    nonlocal minute
    cron = await CronManager.create(
      CronForm(schedule="* * * * *", job_type=job_type, job_parameters={})
    )
    assert cron.id is not None
    assert sum(await asyncio.gather(CronManager.check(), CronManager.check())) == 1
    first = await CronManager.get(cron.id)
    assert first is not None and first.last_job is not None
    assert first.last_scheduled_for == minute
    await JobManager.abort(first.last_job)
    # Even after completion, the same occurrence cannot be materialized again.
    assert await CronManager.check() == 0
    minute += timedelta(minutes=1)
    assert await CronManager.check() == 1
    second = await CronManager.get(cron.id)
    assert second is not None and second.last_job is not None
    assert second.last_job != first.last_job
    assert second.last_scheduled_for == minute
    minute += timedelta(minutes=1)
    assert await CronManager.check() == 0
    await JobManager.abort(second.last_job)
    # An explicit run is independent of scheduled occurrence progress.
    manual = await CronManager.run_now(cron.id)
    current = await CronManager.get(cron.id)
    assert current is not None
    assert current.last_scheduled_for == second.last_scheduled_for
    assert current.last_job == second.last_job and manual.id != second.last_job

  execution_runtime.run(scenario())


def test_overdue_execution_is_closed_using_persisted_database_time(
  job_type, execution_runtime
):
  async def scenario():
    abandoned = await JobManager.create(job_type, {})
    pending = await JobManager.create(job_type, {})
    assert abandoned.id is not None and pending.id is not None
    with TestSession() as session:
      # Independent crash fixture: an executor claimed and then disappeared.
      session.connection().execute(
        sa.text("UPDATE inkcre.jobs SET status = 'running' WHERE id = :id"),
        {"id": abandoned.id},
      )
      session.connection().execute(
        sa.text(
          "UPDATE inkcre.jobs SET started_at = "
          "statement_timestamp() - interval '2 minutes' "
          "WHERE id = :id"
        ),
        {"id": abandoned.id},
      )
      session.commit()
    assert await JobManager.expire_overdue() == 1
    closed = await JobManager.get(abandoned.id)
    unclaimed = await JobManager.get(pending.id)
    assert closed is not None and closed.status == JobStatus.TIMED_OUT
    assert closed.closed_at is not None and not closed.abort_requested
    assert unclaimed is not None and unclaimed.status == JobStatus.PENDING
    assert await JobManager.expire_overdue() == 0

  execution_runtime.run(scenario())
