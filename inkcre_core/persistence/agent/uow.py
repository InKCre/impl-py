"""Short transaction for persisted Agent definitions."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from inkcre_core.context import current_core
from .repository import AgentRepository


@asynccontextmanager
async def agent_uow() -> AsyncGenerator[AgentRepository, None]:
  async with current_core().sessions.begin() as session:
    yield AgentRepository(session)
