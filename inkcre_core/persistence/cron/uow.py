"""A Cron occurrence and its admitted Job commit together."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from inkcre_core.context import current_core
from inkcre_core.persistence.job.repository import JobRepository
from inkcre_core.persistence.job.uow import JobUnitOfWork
from inkcre_core.persistence.source.repository import SourceRepository
from .repository import CronRepository


@dataclass(frozen=True)
class CronUnitOfWork(JobUnitOfWork):
  crons: CronRepository


@asynccontextmanager
async def cron_uow() -> AsyncGenerator[CronUnitOfWork, None]:
  async with current_core().sessions.begin() as session:
    yield CronUnitOfWork(
      JobRepository(session), SourceRepository(session), CronRepository(session)
    )
