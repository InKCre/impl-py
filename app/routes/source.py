"""Source configuration resources and Job admission conveniences."""

import typing

import fastapi

from inkcre_core.business.job import JobManager
from inkcre_core.business.source import SOURCE_BACKFILL_JOB_TYPE, SOURCE_COLLECT_JOB_TYPE
from inkcre_core.business.source.main import (
  SourceManager,
  SourceNotFoundError,
  UnsupportedSourceCommandError,
)
from inkcre_core.schemas.job import JobModel
from inkcre_core.schemas.source import (
  SourceCreateForm,
  SourceModel,
  SourceTypesModel,
  SourceUpdateForm,
)

from .validation import database_write, request_input


ROUTER = fastapi.APIRouter(tags=["source"])


@ROUTER.get("/source-types")
async def list_source_types(
  limit: int | None = fastapi.Query(None, gt=0), cursor: str | None = None
) -> dict[str, typing.Any]:
  rows, next_cursor = await SourceManager.list_types(limit=limit, cursor=cursor)
  return {"source_types": rows, "next_cursor": next_cursor}


@ROUTER.get("/source-types/{type_}")
async def get_source_type(type_: str) -> SourceTypesModel:
  result = await SourceManager.get_type(type_)
  if result is None:
    raise fastapi.HTTPException(404, f"Source type {type_!r} not found")
  return result


@ROUTER.get("/sources")
async def list_sources(
  limit: int | None = fastapi.Query(None, gt=0), cursor: int | None = None
) -> dict[str, typing.Any]:
  rows, next_cursor = await SourceManager.list_sources(limit=limit, cursor=cursor)
  return {"sources": rows, "next_cursor": next_cursor}


@ROUTER.get("/sources/{source_id}")
async def get_source(source_id: int) -> SourceModel:
  result = await SourceManager.get(source_id)
  if result is None:
    raise fastapi.HTTPException(404, f"Source {source_id} not found")
  return result


@ROUTER.post("/sources", status_code=201)
async def create_source(
  body: SourceCreateForm, request: fastapi.Request, response: fastapi.Response
) -> SourceModel:
  try:
    with database_write(), request_input():
      source = await SourceManager.create(
        body.type, body.nickname, body.config, body.storage
      )
  except SourceNotFoundError as error:
    raise fastapi.HTTPException(404, str(error)) from error
  response.headers["Location"] = str(request.url_for("get_source", source_id=source.id))
  return source


@ROUTER.patch("/sources/{source_id}")
async def update_source(source_id: int, body: SourceUpdateForm) -> SourceModel:
  try:
    with database_write(), request_input():
      return await SourceManager.update(source_id, body)
  except SourceNotFoundError as error:
    raise fastapi.HTTPException(404, str(error)) from error


@ROUTER.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int) -> None:
  if not await SourceManager.delete(source_id):
    raise fastapi.HTTPException(404, f"Source {source_id} not found")


async def _collect_job(
  source_id: int, type_: str, config: dict, timeout: int | None
) -> JobModel:
  try:
    with request_input(strip=("config",)):
      return await JobManager.create(
        type_, {"source": source_id, "config": config}, timeout
      )
  except SourceNotFoundError as error:
    raise fastapi.HTTPException(404, str(error)) from error
  except UnsupportedSourceCommandError as error:
    raise fastapi.HTTPException(422, str(error)) from error


@ROUTER.post("/sources/{source_id}/collect", status_code=201)
async def run_source_collect(  # noqa: PLR0913 - includes FastAPI request/response injection
  source_id: int,
  request: fastapi.Request,
  response: fastapi.Response,
  background: fastapi.BackgroundTasks,
  body: dict = fastapi.Body(default_factory=dict),
  timeout_seconds: int | None = fastapi.Query(None, gt=0),
) -> JobModel:
  job = await _collect_job(source_id, SOURCE_COLLECT_JOB_TYPE, body, timeout_seconds)
  response.headers["Location"] = str(request.url_for("get_job", job_id=job.id))
  background.add_task(JobManager.notify_worker)
  return job


@ROUTER.post("/sources/{source_id}/backfill", status_code=201)
async def run_source_backfill(  # noqa: PLR0913 - includes FastAPI request/response injection
  source_id: int,
  body: dict,
  request: fastapi.Request,
  response: fastapi.Response,
  background: fastapi.BackgroundTasks,
  timeout_seconds: int | None = fastapi.Query(None, gt=0),
) -> JobModel:
  job = await _collect_job(source_id, SOURCE_BACKFILL_JOB_TYPE, body, timeout_seconds)
  response.headers["Location"] = str(request.url_for("get_job", job_id=job.id))
  background.add_task(JobManager.notify_worker)
  return job
