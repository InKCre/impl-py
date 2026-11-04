"""Source state, graph and storage participate in one short transaction."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from inkcre_core.context import current_core
from inkcre_core.persistence.info_base.uow import GraphUnitOfWork
from inkcre_core.persistence.info_base.repository import BlockRepository, RelationRepository
from inkcre_core.persistence.info_base.storage import StorageRepository
from inkcre_core.persistence.deployment_config.repository import DeploymentConfigRepository
from .repository import SourceRepository


@dataclass(frozen=True)
class SourceUnitOfWork:
  sources: SourceRepository
  graph: GraphUnitOfWork
  configuration: DeploymentConfigRepository


@asynccontextmanager
async def source_uow() -> AsyncGenerator[SourceUnitOfWork, None]:
  async with current_core().sessions.begin() as session:
    yield SourceUnitOfWork(
      SourceRepository(session),
      GraphUnitOfWork(
        BlockRepository(session), RelationRepository(session), StorageRepository(session)
      ),
      DeploymentConfigRepository(session),
    )
