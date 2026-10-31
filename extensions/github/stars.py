"""GitHub Stars and Lists Source orchestration."""

from __future__ import annotations

import pydantic

from app.business.source import SourceBase
from app.persistence.source.uow import source_uow
from app.schemas.job import JobModel

from .adapter import GitHubGraphQLAdapter
from .reconcile import GitHubGraphReconciler
from .schema import GitHubSourceConfig


class Source(
  SourceBase[GitHubSourceConfig],
  config_cls=GitHubSourceConfig,
):
  """Synchronize the authenticated GitHub Account's Stars and Lists."""

  source_type = "extensions.github.stars.Source"

  async def collect(self, job: JobModel, config: pydantic.BaseModel) -> None:
    del config
    source_config = await self.get_config()
    async with GitHubGraphQLAdapter(source_config.github_token) as adapter:
      snapshot = await adapter.fetch_snapshot()

    async with source_uow() as uow:
      report = await GitHubGraphReconciler(uow).reconcile(self._id, snapshot)
    job.state = report.model_dump(mode="json")


__all__ = ["Source"]
