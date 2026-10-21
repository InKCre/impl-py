"""Capture bytes, graph and receipt commit together on one private session."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.engine import AsyncSessionFactory
from app.persistence.info_base.repository import BlockRepository, RelationRepository
from app.persistence.info_base.storage import StorageRepository
from app.persistence.info_base.uow import GraphUnitOfWork

from .repository import CaptureReceiptRepository


@dataclass(frozen=True)
class CaptureUnitOfWork:
  graph: GraphUnitOfWork
  receipts: CaptureReceiptRepository


@asynccontextmanager
async def capture_uow() -> AsyncGenerator[CaptureUnitOfWork, None]:
  async with AsyncSessionFactory.begin() as session:
    yield CaptureUnitOfWork(
      GraphUnitOfWork(
        BlockRepository(session), RelationRepository(session), StorageRepository(session)
      ),
      CaptureReceiptRepository(session),
    )
