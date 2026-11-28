"""Run Source commands through the production Job lifecycle and read their receipts."""

import pytest
import sqlmodel

from inkcre_core.business.job import JobManager
from inkcre_core.business.source import SOURCE_COLLECT_JOB_TYPE
from inkcre_core.schemas.job import JobModel
from tests.database import TestSession


@pytest.fixture
def run_source_job(async_runner):
  job_ids = []
  JobManager.start()
  try:
    async_runner.run(JobManager.sync_job_types())

    async def run(source_id, config=None, *, job_type=SOURCE_COLLECT_JOB_TYPE):
      job = await JobManager.create(job_type, {"source": source_id, "config": config or {}})
      assert job.id is not None
      job_ids.append(job.id)
      assert await JobManager.run(job.id)
      saved = await JobManager.get(job.id)
      assert saved is not None
      return saved

    yield run
  finally:
    async_runner.run(JobManager.shutdown())
    if job_ids:
      with TestSession() as db:
        db.connection().execute(
          sqlmodel.delete(JobModel).where(sqlmodel.col(JobModel.id).in_(job_ids))
        )
        db.commit()
