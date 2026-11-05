"""MCP projection over live Block, Storage, and Resolver authority."""

from __future__ import annotations

import base64
import dataclasses
import datetime
import enum
import json
import typing

import pydantic

from inkcre_core.business.info_base.resolver import ResolverManager
from inkcre_core.schemas.info_base.block import BlockModel

from .contracts import ContentMode


INLINE_BUDGET = 64 * 1024


def block_preview(block: BlockModel) -> dict[str, typing.Any]:
  return {
    "id": block.id,
    "resolver": block.resolver,
    "storage": block.storage,
    "created_at": block.created_at.isoformat(),
    "updated_at": block.updated_at.isoformat(),
  }


def relation_preview(relation: typing.Any) -> dict[str, typing.Any]:
  return {
    "id": relation.id,
    "from_block_id": relation.from_,
    "to_block_id": relation.to_,
    "content": relation.content,
    "updated_at": relation.updated_at.isoformat(),
  }


def encode_selector(path: tuple[str | int, ...]) -> str:
  if not path:
    return "root"
  encoded = base64.urlsafe_b64encode(json.dumps(path).encode()).decode().rstrip("=")
  return encoded


def encode_json(value: typing.Any) -> str:
  return (
    base64.urlsafe_b64encode(
      json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    )
    .decode()
    .rstrip("=")
  )


def decode_json(value: str) -> typing.Any:
  padding = "=" * (-len(value) % 4)
  return json.loads(base64.urlsafe_b64decode(value + padding))


def decode_selector(selector: str) -> tuple[str | int, ...]:
  if selector == "root":
    return ()
  padding = "=" * (-len(selector) % 4)
  value = json.loads(base64.urlsafe_b64decode(selector + padding))
  if not isinstance(value, list) or any(not isinstance(item, str | int) for item in value):
    raise ValueError("Invalid content selector")
  return tuple(value)


def content_uri(
  block_id: int,
  mode: ContentMode,
  kind: typing.Literal["text", "blob"],
  path: tuple[str | int, ...] = (),
) -> str:
  return f"inkcre://blocks/{block_id}/content/{mode}/{kind}/{encode_selector(path)}"


def resolver_method_uri(
  block_id: int,
  method: str,
  arguments: dict[str, typing.Any],
  kind: typing.Literal["text", "blob"],
  path: tuple[str | int, ...] = (),
) -> str:
  return (
    f"inkcre://blocks/{block_id}/resolver/{method}/{encode_json(arguments)}/"
    f"{kind}/{encode_selector(path)}"
  )


async def read_block_value(block: BlockModel, mode: ContentMode) -> typing.Any:
  if mode == "raw":
    return block.content
  if mode == "hydrated":
    return await block.get_hydrated_content()
  return await ResolverManager.get(block).get_solved_content()


def select_value(value: typing.Any, path: tuple[str | int, ...]) -> typing.Any:
  current = value
  for part in path:
    if isinstance(current, pydantic.BaseModel):
      current = getattr(current, typing.cast(str, part))
    elif dataclasses.is_dataclass(current) and not isinstance(current, type):
      current = getattr(current, typing.cast(str, part))
    elif isinstance(current, dict):
      current = current[part]
    elif isinstance(current, list | tuple) and isinstance(part, int):
      current = current[part]
    else:
      raise ValueError("Content selector no longer matches the live value")
  return current


def project_value(
  value: typing.Any,
  *,
  uri_factory: typing.Callable[
    [typing.Literal["text", "blob"], tuple[str | int, ...]], str
  ],
  path: tuple[str | int, ...] = (),
  media_type: str | None = None,
) -> tuple[typing.Any, list[dict[str, typing.Any]]]:
  """Convert one value to JSON facts plus live Resource descriptors."""
  if isinstance(value, bytes):
    uri = uri_factory("blob", path)
    descriptor = {"resource": uri, "size": len(value)}
    resource = {"uri": uri, "size": len(value)}
    if media_type is not None:
      descriptor["mime_type"] = media_type
      resource["mime_type"] = media_type
    return descriptor, [resource]
  if value is None or isinstance(value, str | int | float | bool):
    return value, []
  if isinstance(value, datetime.datetime | datetime.date | datetime.time):
    return value.isoformat(), []
  if isinstance(value, enum.Enum):
    return project_value(
      value.value,
      path=path,
      uri_factory=uri_factory,
      media_type=media_type,
    )
  if isinstance(value, pydantic.BaseModel):
    value = {name: getattr(value, name) for name in value.__class__.model_fields}
  elif dataclasses.is_dataclass(value) and not isinstance(value, type):
    value = {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
  if isinstance(value, dict):
    media_type = next(
      (
        candidate
        for key in ("detected_media_type", "media_type", "mime_type")
        if isinstance((candidate := value.get(key)), str)
      ),
      media_type,
    )
    result: dict[str, typing.Any] = {}
    resources: list[dict[str, typing.Any]] = []
    for key, item in value.items():
      if not isinstance(key, str):
        raise TypeError("Projected mappings must have string keys")
      projected, nested = project_value(
        item,
        path=(*path, key),
        uri_factory=uri_factory,
        media_type=media_type,
      )
      result[key] = projected
      resources.extend(nested)
    return result, resources
  if isinstance(value, list | tuple):
    result_list: list[typing.Any] = []
    resources = []
    for index, item in enumerate(value):
      projected, nested = project_value(
        item,
        path=(*path, index),
        uri_factory=uri_factory,
        media_type=media_type,
      )
      result_list.append(projected)
      resources.extend(nested)
    return result_list, resources
  raise TypeError(f"Unsupported projected content type: {type(value).__name__}")
