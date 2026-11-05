"""Short persistence scope; provider calls run after it closes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from inkcre_core.context import current_core
from .repository import SemanticRetrievalRepository


@asynccontextmanager
async def semantic_retrieval_uow() -> AsyncGenerator[SemanticRetrievalRepository, None]:
  async with current_core().sessions.begin() as session:
    yield SemanticRetrievalRepository(session)
