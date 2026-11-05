"""JSON projection shared by domain-owned Agent Tool controllers."""

import dataclasses
import typing

import pydantic

from inkcre_core.schemas.ai import JSONValue


_JSON_ADAPTER = pydantic.TypeAdapter(JSONValue)


def project_json(value: typing.Any) -> JSONValue:
  """Project ordinary domain values without admitting binary Tool results."""
  if _contains_bytes(value):
    raise TypeError("Binary values are unavailable through JSON Agent Tools")
  if isinstance(value, pydantic.BaseModel):
    projected = value.model_dump(mode="json")
  elif dataclasses.is_dataclass(value) and not isinstance(value, type):
    projected = dataclasses.asdict(value)
  else:
    projected = pydantic.TypeAdapter(typing.Any).dump_python(
      typing.cast(typing.Any, value),
      mode="json",
    )
  return _JSON_ADAPTER.validate_python(projected)


def _contains_bytes(value: typing.Any) -> bool:
  if isinstance(value, bytes):
    return True
  if isinstance(value, pydantic.BaseModel):
    return any(
      _contains_bytes(getattr(value, field)) for field in value.__class__.model_fields
    )
  if dataclasses.is_dataclass(value) and not isinstance(value, type):
    return any(
      _contains_bytes(getattr(value, field.name)) for field in dataclasses.fields(value)
    )
  if isinstance(value, dict):
    return any(_contains_bytes(item) for item in value.values())
  if isinstance(value, list | tuple | set | frozenset):
    return any(_contains_bytes(item) for item in value)
  return False
