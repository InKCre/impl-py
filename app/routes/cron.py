"""Ordinary deployment-wide Cron resources."""

import contextlib
import typing

import fastapi

from inkcre_core.business.cron import (
  CronManager,
  CronNotFoundError,
  InvalidCronScheduleError,
)
from inkcre_core.business.job import JobManager, UnknownJobTypeError
from inkcre_core.business.source.main import (
  SourceNotFoundError,
  UnsupportedSourceCommandError,
)
from inkcre_core.schemas.cron import CronForm, CronModel, CronUpdateForm
from inkcre_core.schemas.job import JobModel

from .validation import request_input


ROUTER = fastapi.APIRouter(prefix="/crons", tags=["cron"])


@contextlib.contextmanager
def _template_input():
  try:
    with request_input():
      yield
  except (CronNotFoundError, SourceNotFoundError) as error:
    raise fastapi.HTTPException(404, str(error)) from error
  except (
    InvalidCronScheduleError,
    UnknownJobTypeError,
    UnsupportedSourceCommandError,
  ) as error:
    raise fastapi.HTTPException(422, str(error)) from error


@ROUTER.get("")
async def list_crons(
  limit: int | None = fastapi.Query(None, gt=0), cursor: int | None = None
) -> dict[str, typing.Any]:
  rows, next_cursor = await CronManager.list_crons(limit=limit, cursor=cursor)
  return {"crons": rows, "next_cursor": next_cursor}


@ROUTER.get("/{cron_id}")
async def get_cron(cron_id: int) -> CronModel:
  result = await CronManager.get(cron_id)
  if result is None:
    raise fastapi.HTTPException(404, f"Cron {cron_id} not found")
  return result


@ROUTER.post("", status_code=201)
async def create_cron(
  body: CronForm, request: fastapi.Request, response: fastapi.Response
) -> CronModel:
  with _template_input():
    result = await CronManager.create(body)
  response.headers["Location"] = str(request.url_for("get_cron", cron_id=result.id))
  return result


@ROUTER.patch("/{cron_id}")
async def update_cron(cron_id: int, body: CronUpdateForm) -> CronModel:
  with _template_input():
    return await CronManager.patch(cron_id, body)


@ROUTER.delete("/{cron_id}", status_code=204)
async def delete_cron(cron_id: int) -> None:
  if not await CronManager.delete(cron_id):
    raise fastapi.HTTPException(404, f"Cron {cron_id} not found")


@ROUTER.post("/{cron_id}/run", status_code=201)
async def run_cron(
  cron_id: int,
  request: fastapi.Request,
  response: fastapi.Response,
  background: fastapi.BackgroundTasks,
) -> JobModel:
  with _template_input():
    job = await CronManager.run_now(cron_id)
  response.headers["Location"] = str(request.url_for("get_job", job_id=job.id))
  background.add_task(JobManager.notify_worker)
  return job
