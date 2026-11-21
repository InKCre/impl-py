"""Coordinate externally stored bytes with a subsequent graph write."""

from abc import abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
import logging
from uuid import UUID

import sqlmodel

from inkcre_core.persistence.info_base.storage import StorageRepository

from .main import WritableStorage

logger = logging.getLogger(__name__)


def serialize_uuid_pointer(pointer: str) -> str:
  parsed = UUID(pointer)
  if parsed.version != 4 or str(parsed) != pointer:
    raise ValueError("Storage object pointer must be a canonical UUIDv4")
  return pointer


class ExternalWritableStorage[ConfigT: sqlmodel.SQLModel](WritableStorage[ConfigT, bytes]):
  @abstractmethod
  async def get_raw_content(self, block_content: str) -> bytes: ...

  async def read_content(self, block_content: str, storage: StorageRepository) -> bytes:
    return await self.get_raw_content(block_content)

  @abstractmethod
  async def write_content(
    self, content: bytes, storage: StorageRepository | None
  ) -> str: ...

  @abstractmethod
  async def delete_content(
    self, block_content: str, storage: StorageRepository | None
  ) -> bool: ...

  @asynccontextmanager
  async def prepare_write(
    self, content: bytes
  ) -> AsyncGenerator[Callable[[StorageRepository], Awaitable[str]], None]:
    pointer = self.serialize_pointer(await self.write_content(content, None))
    adopted = False

    async def pointer_for(repository: StorageRepository) -> str:
      nonlocal adopted
      adopted = True
      return pointer

    try:
      yield pointer_for
    finally:
      # Once the graph receives the pointer, a lost commit response can leave it referenced.
      if not adopted:
        try:
          await self.delete_content(pointer, None)
        except Exception:
          logger.warning(
            "Could not discard unused object %s in storage %s", pointer, self.storage_id
          )
