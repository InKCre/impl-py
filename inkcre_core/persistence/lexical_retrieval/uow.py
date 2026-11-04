"""Short lexical database scope, separate from Resolver projection."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from inkcre_core.context import current_core
from .repository import LexicalRepository


@asynccontextmanager
async def lexical_uow() -> AsyncGenerator[LexicalRepository, None]:
  async with current_core().sessions.begin() as session:
    yield LexicalRepository(session)
