"""Job admission also reads Source contracts within its transaction."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from inkcre_core.context import current_core
from inkcre_core.persistence.source.repository import SourceRepository
from .repository import JobRepository


@dataclass(frozen=True)
class JobUnitOfWork:
  jobs: JobRepository
  sources: SourceRepository


@asynccontextmanager
async def job_uow() -> AsyncGenerator[JobUnitOfWork, None]:
  async with current_core().sessions.begin() as session:
    yield JobUnitOfWork(JobRepository(session), SourceRepository(session))
