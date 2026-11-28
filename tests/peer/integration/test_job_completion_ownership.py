"""Observe Job closure across independent Peers sharing a migrated PostgreSQL."""

import asyncio
import os

import pydantic
import pytest

from inkcre_core import Core
from inkcre_core.business.job import JobHandler, JobManager
from inkcre_core.schemas.job import JobModel, JobStatus


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)


class Parameters(pydantic.BaseModel):
  pass


def test_discovery_leaves_timed_out_execution_running_until_cleanup(
  async_runner, monkeypatch
):
  # Each Core owns independent execution state and database connections.
  async def check() -> None:
    executor = Core(os.environ["INKCRE_TEST_DATABASE_URL"])
    observer = Core(os.environ["INKCRE_TEST_DATABASE_URL"])
    executor.accepting_jobs = observer.accepting_jobs = True
    cleaning = asyncio.Event()
    release = asyncio.Event()

    class SlowCleanup(
      JobHandler,
      job_type="test.peer.job.slow_cleanup.v1",
      description="Execution whose resources outlive its deadline",
      parameters_model=Parameters,
      default_timeout_seconds=1,
    ):
      @classmethod
      async def can_handle(cls, parameters: Parameters) -> bool:
        return True

      @classmethod
      async def handle(cls, job: JobModel, parameters: Parameters) -> None:
        try:
          await asyncio.Event().wait()
        finally:
          cleaning.set()
          await release.wait()
          job.state = {"resources_released": True}

    try:
      with executor.bind():
        await JobManager.sync_job_types()
        submitted = await JobManager.create(SlowCleanup.type, {})
        assert submitted.id is not None
        running = asyncio.create_task(JobManager.run(submitted.id))
      try:
        await asyncio.wait_for(cleaning.wait(), timeout=8)
        with observer.bind():
          await JobManager.check()
          during = await JobManager.get(submitted.id)
          assert during is not None
          assert (during.status, during.closed_at) == (JobStatus.RUNNING, None)
      finally:
        release.set()
        assert await asyncio.wait_for(running, timeout=8)
      with observer.bind():
        after = await JobManager.get(submitted.id)
        assert after is not None
        assert after.status == JobStatus.TIMED_OUT
        assert after.closed_at is not None
        assert after.state == {"resources_released": True}
    finally:
      await executor.aclose()
      await observer.aclose()

  monkeypatch.setattr(JobManager, "_handlers", {})
  async_runner.run(check())


def test_handler_timeout_is_failure_when_execution_budget_has_not_elapsed(
  async_runner, monkeypatch
):
  async def check() -> None:
    executor = Core(os.environ["INKCRE_TEST_DATABASE_URL"])
    executor.accepting_jobs = True

    class OperationTimeout(
      JobHandler,
      job_type="test.peer.job.operation_timeout.v1",
      description="Operation whose own timeout precedes the execution deadline",
      parameters_model=Parameters,
      default_timeout_seconds=60,
    ):
      @classmethod
      async def can_handle(cls, parameters: Parameters) -> bool:
        return True

      @classmethod
      async def handle(cls, job: JobModel, parameters: Parameters) -> None:
        raise TimeoutError("upstream operation timed out")

    try:
      with executor.bind():
        await JobManager.sync_job_types()
        submitted = await JobManager.create(OperationTimeout.type, {})
        assert submitted.id is not None
        assert await JobManager.run(submitted.id)
        closed = await JobManager.get(submitted.id)
        assert closed is not None
        assert closed.status == JobStatus.FAILED
        assert closed.state["error"] == "upstream operation timed out"
    finally:
      await executor.aclose()

  monkeypatch.setattr(JobManager, "_handlers", {})
  async_runner.run(check())
