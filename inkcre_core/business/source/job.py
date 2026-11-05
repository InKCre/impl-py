"""Exact Source command handlers hosted by the global Job runtime."""

from inkcre_core.business.job import JobHandler
from inkcre_core.persistence.job.uow import JobUnitOfWork
from inkcre_core.schemas.job import JobModel
from inkcre_core.schemas.source import (
  SourceBackfillParameters,
  SourceCollectParameters,
)

from inkcre_core.validation import input_path

from .main import SourceManager, SourceNotFoundError


SOURCE_COLLECT_JOB_TYPE = "core.source.collect.v1"
SOURCE_BACKFILL_JOB_TYPE = "core.source.backfill.v1"


async def _source_type(parameters: SourceCollectParameters) -> str | None:
  source = await SourceManager.get(parameters.source)
  return None if source is None else source.type


class SourceCollectJobHandler(
  JobHandler[SourceCollectParameters],
  job_type=SOURCE_COLLECT_JOB_TYPE,
  description="Run one ordinary collection command for a configured Source.",
  parameters_model=SourceCollectParameters,
  default_timeout_seconds=300,
):
  @classmethod
  async def normalize_parameters(cls, parameters: dict, uow: JobUnitOfWork) -> dict:
    normalized = await super().normalize_parameters(parameters, uow)
    source = await uow.sources.get(normalized["source"])
    if source is None:
      raise SourceNotFoundError(f"Source {normalized['source']} does not exist")
    with input_path("config"):
      normalized["config"] = SourceManager.normalize_config(
        source.type,
        normalized["config"],
        await uow.sources.get_type(source.type),
        command="collect",
      )
    return normalized

  @classmethod
  async def can_handle(cls, parameters: SourceCollectParameters) -> bool:
    source_type = await _source_type(parameters)
    return source_type is not None and SourceManager.has_source_type(source_type)

  @classmethod
  async def handle(cls, job: JobModel, parameters: SourceCollectParameters) -> None:
    source = await SourceManager.get_source_ins(parameters.source)
    config = source.validate_collect_config(parameters.config)
    await source.collect(job, config)


class SourceBackfillJobHandler(
  JobHandler[SourceBackfillParameters],
  job_type=SOURCE_BACKFILL_JOB_TYPE,
  description="Run one exact historical collection command for a configured Source.",
  parameters_model=SourceBackfillParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def normalize_parameters(cls, parameters: dict, uow: JobUnitOfWork) -> dict:
    normalized = await super().normalize_parameters(parameters, uow)
    source = await uow.sources.get(normalized["source"])
    if source is None:
      raise SourceNotFoundError(f"Source {normalized['source']} does not exist")
    with input_path("config"):
      normalized["config"] = SourceManager.normalize_config(
        source.type,
        normalized["config"],
        await uow.sources.get_type(source.type),
        command="backfill",
      )
    return normalized

  @classmethod
  async def can_handle(cls, parameters: SourceBackfillParameters) -> bool:
    source_type = await _source_type(parameters)
    return source_type is not None and SourceManager.supports_backfill(source_type)

  @classmethod
  async def handle(cls, job: JobModel, parameters: SourceBackfillParameters) -> None:
    source = await SourceManager.get_source_ins(parameters.source)
    config = source.validate_backfill_config(parameters.config)
    await source.backfill(job, config)
