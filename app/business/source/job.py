"""Exact Source command handlers hosted by the global Job runtime."""

from app.contracts.jobs import (
  SOURCE_COLLECT_JOB_TYPE as SOURCE_COLLECT_JOB_TYPE,
  SOURCE_BACKFILL_JOB_TYPE as SOURCE_BACKFILL_JOB_TYPE,
)

from app.business.job import JobHandler
from app.persistence.job.uow import JobUnitOfWork
from app.schemas.job import JobModel
from app.schemas.source import (
  SourceBackfillParameters,
  SourceCollectParameters,
)

from app.validation import input_path

from .main import SourceManager, SourceNotFoundError


async def _source_type(parameters: SourceCollectParameters) -> str | None:
  source = await SourceManager.get(parameters.source)
  return None if source is None else source.type


class SourceCollectJobHandler(
  JobHandler[SourceCollectParameters],
  job_type=SOURCE_COLLECT_JOB_TYPE,
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
