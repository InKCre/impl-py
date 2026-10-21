"""Storage catalog and byte persistence bound to the enclosing graph transaction."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import sqlalchemy
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
import sqlmodel

from app.schemas.info_base.storage import StorageBlobModel, StorageModel, StorageTypesModel


class StorageRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def get(self, storage_id: int) -> StorageModel:
    return (
      await self._session.scalars(
        sqlmodel.select(StorageModel).where(StorageModel.id == storage_id)
      )
    ).one()

  async def sync_types(self, values: Sequence[dict[str, Any]]) -> None:
    if not values:
      return
    statement = insert(StorageTypesModel).values(values)
    await self._session.execute(
      statement.on_conflict_do_update(
        index_elements=[StorageTypesModel.id],
        set_={
          "description": statement.excluded.description,
          "config_schema": statement.excluded.config_schema,
          "writable": statement.excluded.writable,
        },
      )
    )

  async def sync_builtins(self, values: Sequence[dict[str, Any]]) -> None:
    if not values:
      return
    statement = insert(StorageModel).values(values)
    await self._session.execute(
      statement.on_conflict_do_update(
        index_elements=["id"],
        set_={
          "type": statement.excluded.type,
          "nickname": statement.excluded.nickname,
          "config": statement.excluded.config,
        },
      )
    )

  async def create(self, storage: StorageModel) -> StorageModel:
    self._session.add(storage)
    await self._session.flush()
    return storage

  async def read_blob(self, blob_id: UUID, *, lock: bool = False) -> bytes | None:
    statement = sqlmodel.select(StorageBlobModel.data).where(StorageBlobModel.id == blob_id)
    if lock:
      statement = statement.with_for_update()
    return (await self._session.scalars(statement)).one_or_none()

  async def create_blob(self, content: bytes) -> UUID:
    blob = StorageBlobModel(data=content)
    self._session.add(blob)
    await self._session.flush()
    return blob.id

  async def update_blob(self, blob_id: UUID, content: bytes) -> bool:
    result = await self._session.scalars(
      sqlalchemy.update(StorageBlobModel)
      .where(
        sqlmodel.col(StorageBlobModel.id) == blob_id,
      )
      .values(data=content)
      .returning(sqlmodel.col(StorageBlobModel.id))
    )
    return result.one_or_none() is not None

  async def delete_blob(self, blob_id: UUID) -> bool:
    result = await self._session.scalars(
      sqlalchemy.delete(StorageBlobModel)
      .where(
        sqlmodel.col(StorageBlobModel.id) == blob_id,
      )
      .returning(sqlmodel.col(StorageBlobModel.id))
    )
    return result.one_or_none() is not None
