"""Writable PostgreSQL binary storage."""

import uuid

import pydantic
import sqlmodel

from .main import WritableStorage
from app.persistence.info_base.storage import StorageRepository


class PostgreSQLBinaryStorageConfig(sqlmodel.SQLModel):
  pass


class PostgreSQLBlobPointer(pydantic.BaseModel):
  """Minimum pointer fields accepted from storage-backed block content."""

  model_config = pydantic.ConfigDict(extra="ignore")

  blob_id: uuid.UUID


class StorageBlobNotFoundError(LookupError):
  pass


class PostgreSQLBinaryStorage(
  WritableStorage[PostgreSQLBinaryStorageConfig, bytes],
  stg_type="postgresql_binary",
  config_cls=PostgreSQLBinaryStorageConfig,
):
  """Store raw bytes in the protocol database and return an opaque UUID pointer."""

  def serialize_pointer(self, pointer: object) -> str:
    blob_id = pydantic.TypeAdapter(uuid.UUID).validate_python(pointer)
    return PostgreSQLBlobPointer(blob_id=blob_id).model_dump_json()

  async def read_content(
    self, block_content: str, storage: StorageRepository, *, lock: bool = False
  ) -> bytes:
    pointer = PostgreSQLBlobPointer.model_validate_json(block_content)
    content = await storage.read_blob(pointer.blob_id, lock=lock)
    if content is None:
      raise StorageBlobNotFoundError(f"Storage blob {pointer.blob_id} not found")
    return content

  async def write_content(self, content: bytes, storage: StorageRepository) -> uuid.UUID:
    return await storage.create_blob(content)

  async def update_content(
    self, block_content: str, content: bytes, storage: StorageRepository
  ) -> bool:
    pointer = PostgreSQLBlobPointer.model_validate_json(block_content)
    return await storage.update_blob(pointer.blob_id, content)

  async def delete_content(self, block_content: str, storage: StorageRepository) -> bool:
    pointer = PostgreSQLBlobPointer.model_validate_json(block_content)
    return await storage.delete_blob(pointer.blob_id)
