"""Short database scope for AI facts; provider calls outlive this scope."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from inkcre_core.context import current_core
from .repository import AIRepository


@asynccontextmanager
async def ai_uow() -> AsyncGenerator[AIRepository, None]:
  async with current_core().sessions.begin() as session:
    yield AIRepository(session)
