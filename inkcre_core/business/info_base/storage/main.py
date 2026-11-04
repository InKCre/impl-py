__all__ = [
  "StorageManager",
  "Storage",
]

import abc
import importlib
import sqlmodel
import typing
from inkcre_core.persistence.info_base.storage import StorageRepository
from inkcre_core.database_contract.profile import (
  BUILTIN_STORAGES,
  BUILTIN_STORAGE_TYPES_BY_ID,
)
from inkcre_core.schemas.info_base.storage import (
  StorageID,
  StorageTypeID,
  StorageModel,
)


ConfigTV = typing.TypeVar("ConfigTV", bound=sqlmodel.SQLModel)
ContentTV = typing.TypeVar("ContentTV")


class _EmptyConfig(sqlmodel.SQLModel):
  """Default empty config class for storages without configuration."""


class StorageManager:
  _STORAGE_CLASSES: dict[StorageTypeID, type["Storage"]] = {}
  """Storage type registry: type ID -> Storage class"""

  @classmethod
  def register_storage(cls, storage_cls: type["Storage"]):
    """Register a storage class in memory without external side effects."""
    cls._STORAGE_CLASSES[storage_cls.__stgtype__] = storage_cls

  @classmethod
  async def get_storage_async(cls, storage_id: StorageID) -> "Storage":
    from inkcre_core.persistence.info_base.uow import graph_uow

    async with graph_uow() as uow:
      record = await uow.storage.get(storage_id)
    return cls.from_record(record)

  @classmethod
  def from_record(cls, record: StorageModel) -> "Storage":
    storage_class = cls._STORAGE_CLASSES.get(record.type)
    if storage_class is None:
      module_path, class_name = record.type.rsplit(".", 1)
      storage_class = getattr(importlib.import_module(module_path), class_name)
    return storage_class(record)

  @classmethod
  async def setup_builtin_storages_async(cls) -> None:
    from inkcre_core.persistence.info_base.uow import graph_uow

    records = []
    for storage_cls in cls._STORAGE_CLASSES.values():
      builtin = BUILTIN_STORAGE_TYPES_BY_ID.get(storage_cls.__stgtype__)
      records.append(
        {
          "id": storage_cls.__stgtype__,
          "description": builtin.description
          if builtin
          else storage_cls.__doc__ or "No description.",
          "config_schema": builtin.config_schema
          if builtin
          else storage_cls.__configschema__,
          "writable": issubclass(storage_cls, WritableStorage),
        }
      )
    async with graph_uow() as uow:
      await uow.storage.sync_types(records)
      await uow.storage.sync_builtins(
        [
          {"id": row.id, "type": row.type, "nickname": row.nickname, "config": row.config}
          for row in BUILTIN_STORAGES
        ]
      )


class Storage(abc.ABC, typing.Generic[ConfigTV, ContentTV]):
  """Storage base.
  Storage retrieves the raw ("real") content from block record.

  Generic parameters:
    - ConfigTV: Configuration type variable
    - ContentTV: Content type variable
  """

  __configschema__: dict
  """Storage configuration JSON schema"""
  __configcls__: type[ConfigTV]
  __stgtype__: StorageTypeID
  """Storage type identifier"""

  def __init_subclass__(
    cls,
    stg_type: StorageTypeID | None = None,
    config_cls: type[ConfigTV] = _EmptyConfig,
    **kwargs,
  ) -> None:
    """
    :param stg_type: Unique storage type string
    :param config_cls: Configuration class for the storage
    """
    # ConfigTV is bound by the concrete storage subclass.
    if stg_type is not None:
      cls.__configcls__ = config_cls  # pyrefly: ignore[no-access]
      cls.__configschema__ = config_cls.model_json_schema()
      cls.__stgtype__ = stg_type
      StorageManager.register_storage(cls)
    return super().__init_subclass__(**kwargs)

  def __init__(self, storage_record: StorageModel):
    if storage_record.id is None:
      raise ValueError("Storage must be persisted before use")
    self._id = storage_record.id
    self._config = self.__configcls__.model_validate(storage_record.config)

    self.__post_init__()

  @property
  def storage_id(self) -> StorageID:
    """Return the persisted Storage reference represented by this instance."""
    return self._id

  def __post_init__(self):
    """Post-initialization hook for subclasses."""
    pass

  async def get_raw_content(self, block_content: str) -> ContentTV:
    """Get the raw content of the block."""
    raise NotImplementedError(
      f"{self.__class__.__name__}.get_raw_content() must be implemented by subclasses."
    )

  def get_transfer_url(self, block_content: str) -> str | None:
    """Return an optional external byte-transfer hint for AI/provider use.

    The URL is neither content authority nor a promise that every external
    provider can fetch it. Callers must retain hydrated content as the primary
    value and treat this result as an optimization hint.
    """
    del block_content
    return None


class WritableStorage(Storage[ConfigTV, ContentTV], abc.ABC):
  """Storage capability for raw content owned by the current deployment."""

  async def get_raw_content(self, block_content: str) -> ContentTV:
    from inkcre_core.persistence.info_base.uow import graph_uow

    async with graph_uow() as uow:
      return await self.read_content(block_content, uow.storage)

  async def create_content(self, content: ContentTV, storage: StorageRepository) -> str:
    return self.serialize_pointer(await self.write_content(content, storage))

  @abc.abstractmethod
  async def read_content(
    self, block_content: str, storage: StorageRepository
  ) -> ContentTV: ...

  @abc.abstractmethod
  async def write_content(
    self, content: ContentTV, storage: StorageRepository
  ) -> typing.Any: ...

  @abc.abstractmethod
  async def update_content(
    self, block_content: str, content: ContentTV, storage: StorageRepository
  ) -> bool: ...

  @abc.abstractmethod
  async def delete_content(
    self, block_content: str, storage: StorageRepository
  ) -> bool: ...

  @abc.abstractmethod
  def serialize_pointer(self, pointer: typing.Any) -> str:
    """Encode a storage-owned key as one opaque block pointer string."""
    ...
