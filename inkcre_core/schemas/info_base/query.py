"""Exact Block predicates shared by info-base and retrieval owners."""

import typing

import pydantic
from pydantic.json_schema import SkipJsonSchema

PostgresInteger = typing.Annotated[
  pydantic.StrictInt, pydantic.Field(ge=-(2**31), le=2**31 - 1)
]


class ResolverEquality(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  eq: pydantic.StrictStr

  @pydantic.field_validator("eq")
  @classmethod
  def non_empty(cls, value: str) -> str:
    if not value.strip():
      raise ValueError("resolver.eq must be an exact non-empty ID")
    return value


class StorageEquality(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  eq: PostgresInteger


class BlockQueryFilters(pydantic.BaseModel):
  """Block-only exact AND predicates; omission differs from a null equality."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  resolver: ResolverEquality | SkipJsonSchema[None] = None
  storage: StorageEquality | SkipJsonSchema[None] = None

  @pydantic.field_validator("resolver", "storage", mode="before")
  @classmethod
  def reject_null_predicate(cls, value):
    if value is None:
      raise ValueError("A supplied predicate must contain eq, not null")
    return value

  @pydantic.model_serializer(mode="wrap")
  def serialize(self, handler: pydantic.SerializerFunctionWrapHandler) -> dict:
    # Preserve omitted predicates across SDK/HTTP/Peer wire roundtrips.
    return {key: value for key, value in handler(self).items() if value is not None}

  @property
  def has_conditions(self) -> bool:
    return self.resolver is not None or self.storage is not None
