"""Short Sink persistence scope; runtime start/close happen after commit."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from inkcre_core.context import current_core
from .repository import SinkRepository


@asynccontextmanager
async def sink_uow() -> AsyncGenerator[SinkRepository, None]:
  async with current_core().sessions.begin() as session:
    yield SinkRepository(session)
