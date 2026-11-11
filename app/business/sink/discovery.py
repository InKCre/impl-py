"""MCP discovery projection; domain owners retain metadata read authority."""

from __future__ import annotations

import base64
import binascii
import inspect
import typing

import pydantic

from inkcre_core.business.info_base.resolver import ResolverManager
from inkcre_core.business.info_base.services import BlockService
from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.source import SourceManager

DiscoveryCollection = typing.Literal[
  "resolvers",
  "source_types",
  "storage_types",
  "sources",
  "storages",
  "blocks",
]


class _Cursor(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  version: typing.Literal[1] = 1
  collection: DiscoveryCollection
  resolver: str | None
  key: (
    pydantic.StrictStr
    | typing.Annotated[pydantic.StrictInt, pydantic.Field(ge=-(2**31), le=2**31 - 1)]
  )


def _after(
  cursor: str | None,
  collection: DiscoveryCollection,
  resolver: str | None,
) -> str | int | None:
  if cursor is None:
    return None
  try:
    decoded = _Cursor.model_validate_json(
      base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
    )
  except (ValueError, binascii.Error) as error:
    raise ValueError("Invalid discovery cursor") from error
  numeric = collection in {"sources", "storages", "blocks"}
  if (
    decoded.collection != collection
    or decoded.resolver != resolver
    or (type(decoded.key) is int) != numeric
  ):
    raise ValueError("Discovery cursor does not match collection/filter")
  return decoded.key


async def discover(
  collection: DiscoveryCollection,
  *,
  limit: int,
  cursor: str | None,
  resolver: str | None,
) -> dict[str, typing.Any]:
  if not 1 <= limit <= 100:
    raise ValueError("Discovery limit must be between 1 and 100")
  if collection == "blocks":
    if resolver is None or not resolver.strip():
      raise ValueError("blocks discovery requires an exact Resolver ID")
  elif resolver is not None:
    raise ValueError("resolver is only accepted for blocks discovery")
  after = _after(cursor, collection, resolver)
  text_after = typing.cast(str | None, after)
  numeric_after = typing.cast(int | None, after)
  rows: list[dict[str, typing.Any]]
  if collection == "resolvers":
    # Slice identities before introspection; discovery never constructs a Resolver.
    ids = sorted(ResolverManager.RESOLVER_CLS)
    ids = [key for key in ids if text_after is None or key > text_after][: limit + 1]
    rows = [
      {
        "id": key,
        "description": (inspect.getdoc(ResolverManager.RESOLVER_CLS[key]) or "")[:512],
        "registered_locally": True,
        "supports_draft": (
          ResolverManager.RESOLVER_CLS[key].draft_input_model is not None
          and ResolverManager.RESOLVER_CLS[key].draft_description is not None
        ),
      }
      for key in ids
    ]
  elif collection == "source_types":
    rows = await SourceManager.discover_types(limit=limit, after=text_after)
    for row in rows:
      row["registered_locally"] = SourceManager.has_source_type(row["id"])
  elif collection == "storage_types":
    rows = await StorageManager.discover_types(limit=limit, after=text_after)
    for row in rows:
      row["registered_locally"] = row["id"] in StorageManager._STORAGE_CLASSES
  elif collection == "sources":
    rows = await SourceManager.discover_instances(limit=limit, after=numeric_after)
    for row in rows:
      row["registered_locally"] = SourceManager.has_source_type(row["type"])
  elif collection == "storages":
    rows = await StorageManager.discover_instances(limit=limit, after=numeric_after)
    for row in rows:
      row["registered_locally"] = row["type"] in StorageManager._STORAGE_CLASSES
  else:
    rows = await BlockService.discover_blocks(
      typing.cast(str, resolver),
      limit=limit,
      after=numeric_after,
    )
    for row in rows:
      row["entity"] = f"block:{row['id']}"
      row["registered_locally"] = row["resolver"] in ResolverManager.RESOLVER_CLS
      for field in ("created_at", "updated_at"):
        row[field] = row[field].isoformat() if row[field] is not None else None
  more = len(rows) > limit
  rows = rows[:limit]
  next_cursor = None
  if more:
    next_cursor = base64.urlsafe_b64encode(
      _Cursor(collection=collection, resolver=resolver, key=rows[-1]["id"])
      .model_dump_json()
      .encode()
    ).decode()
  return {"collection": collection, "results": rows, "next_cursor": next_cursor}
