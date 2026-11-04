"""Transaction scopes for Peer facts and database-owned leases."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from inkcre_core.context import current_core
from .repository import PeerRepository


@asynccontextmanager
async def peer_uow() -> AsyncGenerator[PeerRepository, None]:
  async with current_core().sessions.begin() as session:
    yield PeerRepository(session)
