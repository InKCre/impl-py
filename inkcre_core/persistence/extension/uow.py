"""Short Extension transaction; publication and acquisition remain outside."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from inkcre_core.context import current_core
from .repository import ExtensionRepository


@asynccontextmanager
async def extension_uow() -> AsyncGenerator[ExtensionRepository, None]:
  async with current_core().sessions.begin() as session:
    yield ExtensionRepository(session)
