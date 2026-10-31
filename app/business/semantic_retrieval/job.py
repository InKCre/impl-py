"""Exact semantic embedding maintenance commands hosted by Jobs."""

from app.contracts.jobs import (
  SEMANTIC_MAINTAIN_JOB_TYPE as SEMANTIC_MAINTAIN_JOB_TYPE,
  SEMANTIC_REBUILD_JOB_TYPE as SEMANTIC_REBUILD_JOB_TYPE,
)

from app.business.job import JobHandler
from app.schemas.job import JobModel
from app.schemas.semantic_retrieval import EmbeddingMaintenanceJobParameters

from .main import SemanticRetrievalManager


class SemanticMaintainJobHandler(
  JobHandler[EmbeddingMaintenanceJobParameters],
  job_type=SEMANTIC_MAINTAIN_JOB_TYPE,
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
