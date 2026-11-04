"""Exact lexical projection maintenance commands hosted by Jobs."""

from inkcre_core.business.info_base.resolver import ResolverManager
from inkcre_core.business.job import JobHandler
from inkcre_core.schemas.job import JobModel
from inkcre_core.schemas.lexical_retrieval import LexicalMaintenanceJobParameters

from .main import LexicalRetrievalManager


LEXICAL_MAINTAIN_JOB_TYPE = "core.feature_retrieval.lexical.maintain.v1"
LEXICAL_REBUILD_JOB_TYPE = "core.feature_retrieval.lexical.rebuild.v1"


class LexicalMaintainJobHandler(
  JobHandler[LexicalMaintenanceJobParameters],
  job_type=LEXICAL_MAINTAIN_JOB_TYPE,
  description="Maintain missing or stale Block lexical projection records.",
  parameters_model=LexicalMaintenanceJobParameters,
  default_timeout_seconds=900,
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
  description="Rebuild Block lexical records present before invocation.",
  parameters_model=LexicalMaintenanceJobParameters,
  default_timeout_seconds=1800,
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
