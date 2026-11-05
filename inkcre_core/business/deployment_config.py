"""Deployment-scoped configuration registry and persistence."""

import typing
import dataclasses

import pydantic

from inkcre_core.persistence.deployment_config.uow import configuration_transaction
from inkcre_core.configuration import ConfigContract
from inkcre_core.schemas.deployment_config import (
  DeploymentConfigKey,
  DeploymentConfigModel,
  DeploymentConfigSchemaID,
  DeploymentConfigView,
)


class UnknownDeploymentConfigSchemaError(LookupError):
  """The current peer has no validator for an exact persisted schema ID."""


class DeploymentConfigSchemaCollisionError(ValueError):
  """Two different local models claimed the same exact schema ID."""


class DeploymentConfigNotFoundError(LookupError):
  """A patch addressed a deployment config that does not exist."""


@dataclasses.dataclass(frozen=True)
class _SchemaRegistration:
  contract: ConfigContract
  keys: tuple[str, ...]


class DeploymentConfigManager:
  """Own exact schema registration and the shared ``configs`` relation."""

  _contracts: dict[DeploymentConfigSchemaID, _SchemaRegistration] = {}

  @classmethod
  def register_schema(
    cls,
    schema_id: DeploymentConfigSchemaID,
    model: type[pydantic.BaseModel],
    *,
    keys: tuple[str, ...] = (),
  ) -> None:
    """Register one exact schema ID idempotently for the same model."""
    existing = cls._contracts.get(schema_id)
    if existing is not None:
      if existing.contract.model is model:
        cls._contracts[schema_id] = _SchemaRegistration(
          existing.contract, tuple(sorted(set(existing.keys) | set(keys)))
        )
        return
      raise DeploymentConfigSchemaCollisionError(
        f"Deployment config schema {schema_id!r} is already registered "
        f"by {existing.contract.model.__qualname__}"
      )
    cls._contracts[schema_id] = _SchemaRegistration(ConfigContract(model), keys)

  @classmethod
  def _contract(
    cls,
    schema_id: DeploymentConfigSchemaID,
  ) -> ConfigContract:
    try:
      return cls._contracts[schema_id].contract
    except KeyError as error:
      raise UnknownDeploymentConfigSchemaError(
        f"Unknown deployment config schema: {schema_id}"
      ) from error

  @classmethod
  def _restore_record(
    cls,
    record: DeploymentConfigModel,
  ) -> pydantic.BaseModel:
    return cls._contract(record.schema_id).validate(record.value)

  @classmethod
  def _view(cls, record: DeploymentConfigModel) -> DeploymentConfigView:
    return DeploymentConfigView(
      key=record.key,
      schema=record.schema_id,
      value=record.value,
      created_at=record.created_at,
      updated_at=record.updated_at,
    )

  @classmethod
  def get_schema(cls, schema_id: str, *, include_schema: bool = True) -> dict:
    contract = cls._contract(schema_id)
    entry = cls._contracts[schema_id]
    result: dict[str, typing.Any] = {
      "id": schema_id,
      "keys": entry.keys,
      "description": contract.model.__doc__ or "",
    }
    if include_schema:
      result["input_schema"] = contract.json_schema()
    return result

  @classmethod
  def list_schemas(
    cls, *, limit: int | None = None, cursor: str | None = None
  ) -> tuple[list[dict], str | None]:
    ids = sorted(key for key in cls._contracts if cursor is None or key > cursor)
    more = limit is not None and len(ids) > limit
    ids = ids[:limit]
    return [cls.get_schema(key, include_schema=False) for key in ids], ids[
      -1
    ] if more else None


class DeploymentConfigService:
  """Deployment configuration use cases over the process-local schema registry."""

  @staticmethod
  async def get(key: DeploymentConfigKey) -> pydantic.BaseModel | None:
    async with configuration_transaction() as configs:
      record = await configs.get(key)
      return None if record is None else DeploymentConfigManager._restore_record(record)

  @staticmethod
  async def read(key: DeploymentConfigKey) -> DeploymentConfigView | None:
    async with configuration_transaction() as configs:
      record = await configs.get(key)
      return None if record is None else DeploymentConfigManager._view(record)

  @staticmethod
  async def replace(
    key: DeploymentConfigKey,
    schema_id: DeploymentConfigSchemaID,
    complete_value: dict[str, typing.Any],
  ) -> DeploymentConfigView:
    result, _ = await DeploymentConfigService.replace_with_status(
      key, schema_id, complete_value
    )
    return result

  @staticmethod
  async def replace_with_status(
    key: DeploymentConfigKey,
    schema_id: DeploymentConfigSchemaID,
    complete_value: dict[str, typing.Any],
  ) -> tuple[DeploymentConfigView, bool]:
    normalized = DeploymentConfigManager._contract(schema_id).normalize(complete_value)
    async with configuration_transaction() as configs:
      record, created = await configs.replace(key, schema_id, normalized)
      result = DeploymentConfigManager._view(record)
    return result, created

  @staticmethod
  async def list_configs(
    *, limit: int | None = None, cursor: str | None = None
  ) -> tuple[list[DeploymentConfigView], str | None]:
    async with configuration_transaction() as configs:
      rows, next_cursor = await configs.list(limit=limit, cursor=cursor)
      return [DeploymentConfigManager._view(row) for row in rows], next_cursor

  @staticmethod
  async def delete(key: DeploymentConfigKey) -> bool:
    async with configuration_transaction() as configs:
      record = await configs.get(key)
      if record is None:
        return False
      await configs.delete(record)
    return True

  @staticmethod
  async def patch(
    key: DeploymentConfigKey, partial_value: dict[str, typing.Any]
  ) -> DeploymentConfigView:
    async with configuration_transaction() as configs:
      record = await configs.get(key, for_update=True)
      if record is None:
        raise DeploymentConfigNotFoundError(f"Deployment config {key!r} does not exist")
      contract = DeploymentConfigManager._contract(record.schema_id)
      record.value = contract.prepare_patch(record.value, partial_value).model_dump(
        mode="json"
      )
      await configs.save(record)
      result = DeploymentConfigManager._view(record)
    return result
