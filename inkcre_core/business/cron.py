"""Application-owned materialization of deployment-wide Cron occurrences."""

from __future__ import annotations

import datetime
import typing
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import croniter  # pyrefly: ignore[untyped-import]
import pydantic

from inkcre_core.business.deployment_config import (
  DeploymentConfigManager,
  DeploymentConfigService,
)
from inkcre_core.business.job import JobManager
from inkcre_core.persistence.cron.uow import cron_uow
from inkcre_core.schemas.cron import CronForm, CronID, CronModel, CronUpdateForm
from inkcre_core.schemas.job import JobModel, JobStatus
from inkcre_core.libs.obsrv.main import get_logger
from inkcre_core.libs.obsrv.telemetry import operation, emit_event
from inkcre_core.validation import input_path


CRON_CONFIG_KEY = "core.cron"
CRON_CONFIG_SCHEMA_ID = "core.cron.config.v1"
LOGGER = get_logger().getChild(__name__)


class CronNotFoundError(LookupError):
  """A Cron template does not exist."""


class InvalidCronScheduleError(ValueError):
  """A submitted schedule is not a five-field UNIX Cron expression."""


class CronDeploymentConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  timezone: str = "UTC"

  @pydantic.field_validator("timezone")
  @classmethod
  def validate_timezone(cls, value: str) -> str:
    try:
      ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
      raise ValueError("timezone must be an installed IANA timezone") from error
    return value


DeploymentConfigManager.register_schema(
  CRON_CONFIG_SCHEMA_ID, CronDeploymentConfig, keys=(CRON_CONFIG_KEY,)
)


class InvalidCronTemplateError(ValueError):
  """A failed template must leave its complete transaction before discovery continues."""


class CronManager:
  """Evaluate only the current minute and atomically create typed Jobs."""

  @classmethod
  async def _timezone(cls) -> ZoneInfo:
    config = await DeploymentConfigService.get(CRON_CONFIG_KEY)
    if config is None:
      return ZoneInfo("UTC")
    return ZoneInfo(typing.cast(CronDeploymentConfig, config).timezone)

  @classmethod
  def _matches(cls, schedule: str, now: datetime.datetime) -> bool:
    return croniter.croniter.match(schedule, now)

  @classmethod
  async def check(cls) -> int:
    """Materialize each matching occurrence in one independent transaction."""
    async with cron_uow() as uow:
      cron_ids = await uow.crons.enabled_ids()
    timezone = await cls._timezone()
    created = 0
    for cron_id in cron_ids:
      if cron_id is None:
        continue
      try:
        created += await cls._materialize(cron_id, timezone)
      except InvalidCronTemplateError:
        LOGGER.exception("Cron Job template is invalid", extra={"cron_id": cron_id})
    return created

  @classmethod
  async def _materialize(cls, cron_id: CronID, timezone: ZoneInfo) -> int:
    with operation("cron.materialize", attributes={"inkcre.cron.id": cron_id}):
      return await cls._materialize_occurrence(cron_id, timezone)

  @classmethod
  async def _materialize_occurrence(cls, cron_id: CronID, timezone: ZoneInfo) -> int:
    async with cron_uow() as uow:
      cron = await uow.crons.get(cron_id, lock=True, skip_locked=True)
      if cron is None or not cron.enabled:
        return 0
      database_now = await uow.crons.database_now()
      occurrence = database_now.astimezone(datetime.UTC).replace(second=0, microsecond=0)
      local_minute = database_now.astimezone(timezone).replace(second=0, microsecond=0)
      try:
        matches = cls._matches(cron.schedule, local_minute)
      except (ValueError, KeyError):
        LOGGER.exception("Invalid persisted Cron schedule", extra={"cron_id": cron.id})
        return 0
      if not matches or cron.last_scheduled_for == occurrence:
        return 0
      if cron.last_job is not None:
        last_job = await uow.jobs.get(cron.last_job)
        if last_job is not None and last_job.status in {
          JobStatus.PENDING,
          JobStatus.RUNNING,
        }:
          return 0
      try:
        job = await JobManager.create_in_uow(
          cron.job_type, cron.job_parameters, cron.job_timeout_seconds, uow=uow
        )
      except Exception as error:
        raise InvalidCronTemplateError(f"Invalid Cron {cron.id} template") from error
      cron.last_job = job.id
      cron.last_scheduled_for = occurrence
      await uow.crons.save(cron)
    emit_event("job.submitted", {"inkcre.job.id": job.id or 0, "inkcre.cron.id": cron_id})
    return 1

  @classmethod
  async def run_now(cls, cron_id: CronID) -> JobModel:
    """Create a Job from one Cron template without changing Cron progress."""
    async with cron_uow() as uow:
      cron = await uow.crons.get(cron_id)
      if cron is None:
        raise CronNotFoundError(f"Cron {cron_id} does not exist")
      job = await JobManager.create_in_uow(
        cron.job_type,
        cron.job_parameters,
        cron.job_timeout_seconds,
        uow=uow,
      )
    emit_event("job.submitted", {"inkcre.job.id": job.id or 0, "inkcre.cron.id": cron_id})
    return job

  @classmethod
  async def create(cls, form: CronForm) -> CronModel:
    """Validate and create one Cron template."""
    if len(form.schedule.split()) != 5 or not croniter.croniter.is_valid(form.schedule):
      raise InvalidCronScheduleError(
        "Cron schedule must be a valid five-field UNIX expression"
      )
    async with cron_uow() as uow:
      cron = CronModel(**form.model_dump())
      with input_path("job_parameters"):
        cron.job_parameters = await JobManager.normalize_parameters(
          form.job_type, form.job_parameters, uow
        )
      await uow.crons.save(cron)
      return cron

  @classmethod
  async def update(cls, cron_id: CronID, form: CronForm) -> CronModel:
    """Validate and replace the editable fields of one Cron template."""
    if len(form.schedule.split()) != 5 or not croniter.croniter.is_valid(form.schedule):
      raise InvalidCronScheduleError(
        "Cron schedule must be a valid five-field UNIX expression"
      )
    async with cron_uow() as uow:
      cron = await uow.crons.get(cron_id)
      if cron is None:
        raise CronNotFoundError(f"Cron {cron_id} does not exist")
      cron.schedule = form.schedule
      cron.enabled = form.enabled
      cron.job_type = form.job_type
      with input_path("job_parameters"):
        cron.job_parameters = await JobManager.normalize_parameters(
          form.job_type, form.job_parameters, uow
        )
      cron.job_timeout_seconds = form.job_timeout_seconds
      await uow.crons.save(cron)
      return cron

  @classmethod
  async def get(cls, cron_id: CronID) -> CronModel | None:
    async with cron_uow() as uow:
      return await uow.crons.get(cron_id)

  @classmethod
  async def list_crons(
    cls, *, limit: int | None = None, cursor: CronID | None = None
  ) -> tuple[list[CronModel], CronID | None]:
    async with cron_uow() as uow:
      return await uow.crons.list(limit=limit, cursor=cursor)

  @classmethod
  async def patch(cls, cron_id: CronID, form: CronUpdateForm) -> CronModel:
    async with cron_uow() as uow:
      cron = await uow.crons.get(cron_id, lock=True)
      if cron is None:
        raise CronNotFoundError(f"Cron {cron_id} does not exist")
      changes = form.model_dump(exclude_unset=True)
      candidate = CronForm.model_validate(
        {
          **{field: getattr(cron, field) for field in CronForm.model_fields},
          **changes,
        }
      )
      if len(candidate.schedule.split()) != 5 or not croniter.croniter.is_valid(
        candidate.schedule
      ):
        raise InvalidCronScheduleError(
          "Cron schedule must be a valid five-field UNIX expression"
        )
      with input_path("job_parameters"):
        parameters = await JobManager.normalize_parameters(
          candidate.job_type, candidate.job_parameters, uow
        )
      for field in changes:
        setattr(cron, field, getattr(candidate, field))
      cron.job_parameters = parameters
      await uow.crons.save(cron)
      return cron

  @classmethod
  async def delete(cls, cron_id: CronID) -> bool:
    async with cron_uow() as uow:
      cron = await uow.crons.get(cron_id)
      if cron is None:
        return False
      await uow.crons.delete(cron)
      return True
