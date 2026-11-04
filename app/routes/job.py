"""Ordinary Job resources; admission is independent of local execution eligibility."""

import typing

import fastapi

from inkcre_core.business.job import JobManager, UnknownJobTypeError
from inkcre_core.business.source.main import (
  SourceNotFoundError,
  UnsupportedSourceCommandError,
)
from app.business.sink import SinkError, SinkNotFoundError
from inkcre_core.schemas.job import JobCreateForm, JobModel, JobStatus, JobTypeModel

from .validation import request_input


ROUTER = fastapi.APIRouter(tags=["job"])


@ROUTER.get("/job-types")
async def list_job_types(
  limit: int | None = fastapi.Query(None, gt=0), cursor: str | None = None
) -> dict[str, typing.Any]:
  rows, next_cursor = await JobManager.list_types(limit=limit, cursor=cursor)
  return {"job_types": rows, "next_cursor": next_cursor}


@ROUTER.get("/job-types/{type_}")
async def get_job_type(type_: str) -> JobTypeModel:
  result = await JobManager.get_type(type_)
  if result is None:
    raise fastapi.HTTPException(404, f"Job type {type_!r} not found")
  return result


@ROUTER.get("/jobs")
async def list_jobs(
  limit: int = fastapi.Query(20, gt=0),
  cursor: int | None = None,
  type_: str | None = fastapi.Query(None, alias="type"),
  status: JobStatus | None = None,
) -> dict[str, typing.Any]:
  rows, next_cursor = await JobManager.list_jobs(
    limit=limit, cursor=cursor, type_=type_, status=status
  )
  return {"jobs": rows, "next_cursor": next_cursor}


@ROUTER.get("/jobs/{job_id}")
async def get_job(job_id: int) -> JobModel:
  result = await JobManager.get(job_id)
  if result is None:
    raise fastapi.HTTPException(404, f"Job {job_id} not found")
  return result


@ROUTER.post("/jobs", status_code=201)
async def create_job(
  body: JobCreateForm,
  request: fastapi.Request,
  response: fastapi.Response,
  background: fastapi.BackgroundTasks,
) -> JobModel:
  try:
    with request_input("parameters"):
      job = await JobManager.create(body.type, body.parameters, body.timeout_seconds)
  except SourceNotFoundError as error:
    raise fastapi.HTTPException(404, str(error)) from error
  except SinkNotFoundError as error:
    raise fastapi.HTTPException(404, str(error)) from error
  except (UnknownJobTypeError, UnsupportedSourceCommandError, SinkError) as error:
    raise fastapi.HTTPException(422, str(error)) from error
  response.headers["Location"] = str(request.url_for("get_job", job_id=job.id))
  background.add_task(JobManager.notify_worker)
  return job


@ROUTER.post("/jobs/{job_id}/abort")
async def abort_job(job_id: int) -> JobModel:
  result = await JobManager.abort(job_id)
  if result is None:
    raise fastapi.HTTPException(404, f"Job {job_id} not found")
  return result
