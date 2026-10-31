"""Exact lexical projection maintenance commands hosted by Jobs."""

from app.contracts.jobs import (
  LEXICAL_MAINTAIN_JOB_TYPE as LEXICAL_MAINTAIN_JOB_TYPE,
  LEXICAL_REBUILD_JOB_TYPE as LEXICAL_REBUILD_JOB_TYPE,
)

from app.business.info_base.resolver import ResolverManager
from app.business.job import JobHandler
from app.schemas.job import JobModel
from app.schemas.lexical_retrieval import LexicalMaintenanceJobParameters

from .main import LexicalRetrievalManager


class LexicalMaintainJobHandler(
  JobHandler[LexicalMaintenanceJobParameters],
  job_type=LEXICAL_MAINTAIN_JOB_TYPE,
):
  @classmethod
  async def can_handle(cls, parameters: LexicalMaintenanceJobParameters) -> bool:
    del parameters
    return bool(ResolverManager.RESOLVER_CLS)

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: LexicalMaintenanceJobParameters,
  ) -> None:
    report = await LexicalRetrievalManager.maintain(parameters.options)
    job.state = report.model_dump(mode="json")


class LexicalRebuildJobHandler(
  JobHandler[LexicalMaintenanceJobParameters],
  job_type=LEXICAL_REBUILD_JOB_TYPE,
):
  @classmethod
  async def can_handle(cls, parameters: LexicalMaintenanceJobParameters) -> bool:
    del parameters
    return bool(ResolverManager.RESOLVER_CLS)

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: LexicalMaintenanceJobParameters,
  ) -> None:
    report = await LexicalRetrievalManager.rebuild(parameters.options)
    job.state = report.model_dump(mode="json")
