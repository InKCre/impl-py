from __future__ import annotations

import abc
import jsonschema  # pyrefly: ignore[untyped-import]
import pydantic
import typing
from typing import Optional as Opt

from app.persistence.source.uow import SourceUnitOfWork, source_uow
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.job import JobModel
from app.schemas.source import SourceModel, SourceID, SourceTypesModel, SourceUpdateForm
from app.validation import input_path
from .resolver import SOURCE_RESOLVER_ID, SourceContent

ConfigTV = typing.TypeVar("ConfigTV", bound=pydantic.BaseModel)


class SourceNotFoundError(LookupError):
  """A Source instance or persisted type does not exist."""


class UnsupportedSourceCommandError(ValueError):
  """The catalog explicitly does not support the requested collection mode."""


class EmptySourceCommandConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")


class SourceBase(abc.ABC, typing.Generic[ConfigTV]):
  """InkCre Source Base class.

  Configuration is loaded from the database. Instantiate a source when it is
  used instead of caching configuration separately.
  """

  __configschema__: dict
  """Source configuration JSON schema"""
  __configcls__: type[ConfigTV]
  __collectconfigcls__: type[pydantic.BaseModel]
  __backfillconfigcls__: type[pydantic.BaseModel] | None
  source_type: typing.ClassVar[str]

  def __init_subclass__(
    cls,
    config_cls: type[ConfigTV],
    collect_config_cls: type[pydantic.BaseModel] = EmptySourceCommandConfig,
    backfill_config_cls: type[pydantic.BaseModel] | None = None,
    **kwargs,
  ) -> None:
    # ConfigTV is bound by the concrete source subclass.
    cls.__configcls__ = config_cls  # pyrefly: ignore[no-access]
    cls.__configschema__ = config_cls.model_json_schema()
    cls.__collectconfigcls__ = collect_config_cls  # pyrefly: ignore[no-access]
    cls.__backfillconfigcls__ = backfill_config_cls  # pyrefly: ignore[no-access]
    # Older third-party wheels keep their historical path-based identity. New
    # declarations can retain that exact value independently of code location.
    cls.source_type = cls.__dict__.get(
      "source_type", cls.__module__ + "." + cls.__qualname__
    )
    if not isinstance(cls.source_type, str) or not cls.source_type:
      raise ValueError("Source type must be a non-empty string")
    SourceManager.add_source_type(cls)
    return super().__init_subclass__(**kwargs)

  def __init__(self, _id: SourceID) -> None:
    self._id = _id

  @abc.abstractmethod
  async def collect(self, job: JobModel, config: pydantic.BaseModel) -> None:
    """Collect new data from the source.

    :param job: The collect job containing config and state.

    Notes:
    - Should not surpress exceptions, raise it.
    """

  async def backfill(self, job: JobModel, config: pydantic.BaseModel) -> None:
    del job, config
    raise NotImplementedError(f"{self.__class__.__name__} does not support backfill")

  async def record(self, data: typing.Any) -> None:
    """Record data passively (e.g., from webhook).

    :param data: The data to record from external source (e.g., webhook payload).

    Notes:
    - Should not suppress exceptions, raise it.
    - Used for passive collection methods like webhooks.
    """
    raise NotImplementedError(f"{self.__class__.__name__} does not support passive record")

  async def get_config(self) -> ConfigTV:
    """Read and validate current Source configuration in a short scope."""
    async with source_uow() as uow:
      source = await uow.sources.get(self._id)
      if source is None:
        raise SourceNotFoundError(f"Source {self._id} does not exist")
      return self.__configcls__.model_validate(source.config)

  def validate_collect_config(self, config: dict) -> pydantic.BaseModel:
    return self.__collectconfigcls__.model_validate(config)

  def validate_backfill_config(self, config: dict) -> pydantic.BaseModel:
    if self.__backfillconfigcls__ is None:
      raise NotImplementedError(f"{self.__class__.__name__} does not support backfill")
    return self.__backfillconfigcls__.model_validate(config)

  async def get_state(self) -> dict:
    async with source_uow() as uow:
      source = await uow.sources.get(self._id)
      if source is None:
        raise SourceNotFoundError(f"Source {self._id} does not exist")
      return source.state or {}

  async def set_state(self, state: dict) -> None:
    async with source_uow() as uow:
      source = await uow.sources.get(self._id, lock=True)
      if source is None:
        raise SourceNotFoundError(f"Source {self._id} does not exist")
      source.state = state
      await uow.sources.save(source)


class SourceManager:
  """

  - Run collect method of all configured sources
  - Add, remove and configure source instances
  - Add, remove sources
  """

  SOURCES: dict[SourceID, SourceBase] = {}
  _SOURCE_CLASSES: dict[str, type[SourceBase]] = {}

  @classmethod
  def add_source_type(cls, source_cls: type[SourceBase]) -> None:
    """Register a source type in memory without external side effects."""
    source_type = source_cls.source_type
    existing = cls._SOURCE_CLASSES.get(source_type)
    if existing is not None and existing is not source_cls:
      raise ValueError(f"Source type {source_type!r} is already registered")
    cls._SOURCE_CLASSES[source_type] = source_cls

  @classmethod
  async def sync_source_types_async(
    cls, source_classes: dict[str, type[SourceBase]] | None = None
  ) -> None:
    registered = cls._SOURCE_CLASSES if source_classes is None else source_classes
    rows = [
      dict(
        id=source_type,
        description=source_cls.__doc__ or "No description.",
        config_schema=source_cls.__configschema__,
        collect_config_schema=source_cls.__collectconfigcls__.model_json_schema(),
        backfill_config_schema=(
          None
          if source_cls.__backfillconfigcls__ is None
          else source_cls.__backfillconfigcls__.model_json_schema()
        ),
      )
      for source_type, source_cls in registered.items()
    ]
    async with source_uow() as uow:
      await uow.sources.sync_types(rows)

  @classmethod
  def has_source_type(cls, source_type: str) -> bool:
    return source_type in cls._SOURCE_CLASSES

  @classmethod
  def supports_backfill(cls, source_type: str) -> bool:
    source_cls = cls._SOURCE_CLASSES.get(source_type)
    return source_cls is not None and source_cls.__backfillconfigcls__ is not None

  @classmethod
  async def get_source_ins(cls, source_id: SourceID) -> SourceBase:
    ins = cls.SOURCES.get(source_id)
    if ins is not None:
      return ins
    source = await cls.get(source_id)
    if source is None:
      raise SourceNotFoundError(f"Source {source_id} does not exist")
    source_class = cls._SOURCE_CLASSES.get(source.type)
    if source_class is None:
      raise ValueError(f"Source class {source.type} not registered.")
    ins = source_class(_id=source_id)
    cls.SOURCES[source_id] = ins
    return ins

  @classmethod
  async def create(
    cls,
    type_: str,
    nickname: Opt[str] = None,
    config: dict | None = None,
    storage: int | None = None,
  ) -> SourceModel:
    async with source_uow() as uow:
      with input_path("config"):
        normalized = cls.normalize_config(
          type_, config or {}, await uow.sources.get_type(type_)
        )
      source = SourceModel(
        type=type_, nickname=nickname, config=normalized, storage=storage
      )
      await uow.sources.save(source)
      return source

  @classmethod
  def normalize_config(
    cls,
    type_: str,
    config: dict,
    catalog: SourceTypesModel | None,
    *,
    command: typing.Literal["collect", "backfill"] | None = None,
  ) -> dict:
    """Accept catalog-only input without requiring local execution capability."""
    if catalog is None:
      raise SourceNotFoundError(f"Source type {type_!r} does not exist")
    source_class = cls._SOURCE_CLASSES.get(type_)
    if command == "backfill":
      schema = catalog.backfill_config_schema
      model = source_class.__backfillconfigcls__ if source_class else None
    elif command == "collect":
      schema = catalog.collect_config_schema
      model = source_class.__collectconfigcls__ if source_class else None
    else:
      schema = catalog.config_schema
      model = source_class.__configcls__ if source_class else None  # pyrefly: ignore[missing-attribute]
    if schema is None:
      raise UnsupportedSourceCommandError(
        f"Source type {type_!r} does not support {command}"
      )
    if model is not None:
      return model.model_validate(config).model_dump(mode="json")
    jsonschema.Draft202012Validator(schema).validate(config)
    return config

  @classmethod
  async def get(cls, source_id: SourceID) -> SourceModel | None:
    async with source_uow() as uow:
      return await uow.sources.get(source_id)

  @classmethod
  async def get_type(cls, type_: str) -> SourceTypesModel | None:
    async with source_uow() as uow:
      return await uow.sources.get_type(type_)

  @classmethod
  async def list_sources(
    cls, *, limit: int | None = None, cursor: SourceID | None = None
  ) -> tuple[list[SourceModel], SourceID | None]:
    async with source_uow() as uow:
      return await uow.sources.list_sources(limit=limit, cursor=cursor)

  @classmethod
  async def list_types(
    cls, *, limit: int | None = None, cursor: str | None = None
  ) -> tuple[list[SourceTypesModel], str | None]:
    async with source_uow() as uow:
      return await uow.sources.list_types(limit=limit, cursor=cursor)

  @classmethod
  async def update(cls, source_id: SourceID, form: SourceUpdateForm) -> SourceModel:
    async with source_uow() as uow:
      source = await uow.sources.get(source_id, lock=True)
      if source is None:
        raise SourceNotFoundError(f"Source {source_id} does not exist")
      changes = form.model_dump(exclude_unset=True)
      if "config" in changes:
        with input_path("config"):
          changes["config"] = cls.normalize_config(
            source.type, changes["config"], await uow.sources.get_type(source.type)
          )
      for field, value in changes.items():
        setattr(source, field, value)
      await uow.sources.save(source)
      if "nickname" in changes and source.block is not None:
        await cls.ensure_block_async(source, uow)
      return source

  @classmethod
  async def delete(cls, source_id: SourceID) -> bool:
    """Delete configuration only; collected graph and scheduled commands remain."""
    async with source_uow() as uow:
      source = await uow.sources.get(source_id)
      if source is None:
        return False
      await uow.sources.delete(source)
    cls.SOURCES.pop(source_id, None)
    return True

  @classmethod
  async def ensure_block_async(
    cls, source: SourceModel, uow: SourceUnitOfWork
  ) -> BlockModel:
    """Create/reuse a Source anchor in the caller's graph/state transaction."""
    if source.id is None:
      raise ValueError("Source must be persisted before creating its anchor")
    locked = await uow.sources.get(source.id, lock=True)
    if locked is None:
      raise SourceNotFoundError(f"Source {source.id} does not exist")
    content = SourceContent(
      id=source.id, type=locked.type, nickname=locked.nickname
    ).model_dump_json()
    if locked.block is None:
      block = await uow.graph.blocks.create(
        BlockForm(resolver=SOURCE_RESOLVER_ID, content=content)
      )
      locked.block = block.id
      await uow.sources.save(locked)
      return block
    block = await uow.graph.blocks.get(locked.block)
    if block is None:
      raise RuntimeError("Source anchor reference does not resolve")
    if block.resolver != SOURCE_RESOLVER_ID or block.content != content:
      block.resolver = SOURCE_RESOLVER_ID
      block.storage = None
      block.content = content
      await uow.graph.blocks.save(block)
    return block
