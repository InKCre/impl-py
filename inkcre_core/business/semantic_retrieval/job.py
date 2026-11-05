"""Exact semantic embedding maintenance commands hosted by Jobs."""

from inkcre_core.business.job import JobHandler
from inkcre_core.schemas.job import JobModel
from inkcre_core.schemas.semantic_retrieval import EmbeddingMaintenanceJobParameters

from .main import SemanticRetrievalManager


SEMANTIC_MAINTAIN_JOB_TYPE = "core.semantic_retrieval.maintain.v1"
SEMANTIC_REBUILD_JOB_TYPE = "core.semantic_retrieval.rebuild.v1"


class SemanticMaintainJobHandler(
  JobHandler[EmbeddingMaintenanceJobParameters],
  job_type=SEMANTIC_MAINTAIN_JOB_TYPE,
  description="Maintain missing or stale semantic embedding records.",
  parameters_model=EmbeddingMaintenanceJobParameters,
  default_timeout_seconds=900,
):
  @classmethod
  async def can_handle(cls, parameters: EmbeddingMaintenanceJobParameters) -> bool:
    return await SemanticRetrievalManager.can_maintain(parameters.profile)

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: EmbeddingMaintenanceJobParameters,
  ) -> None:
    report = await SemanticRetrievalManager.maintain(
      parameters.profile,
      parameters.options,
    )
    job.state = report.model_dump(mode="json")


class SemanticRebuildJobHandler(
  JobHandler[EmbeddingMaintenanceJobParameters],
  job_type=SEMANTIC_REBUILD_JOB_TYPE,
  description="Rebuild semantic embedding records present before invocation.",
  parameters_model=EmbeddingMaintenanceJobParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: EmbeddingMaintenanceJobParameters) -> bool:
    return await SemanticRetrievalManager.can_maintain(parameters.profile)

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: EmbeddingMaintenanceJobParameters,
  ) -> None:
    report = await SemanticRetrievalManager.rebuild(
      parameters.profile,
      parameters.options,
    )
    job.state = report.model_dump(mode="json")
