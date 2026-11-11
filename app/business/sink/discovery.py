"""MCP metadata identities; domain owners retain metadata read authority."""

from __future__ import annotations

import inspect
import typing

from inkcre_core.business.info_base.resolver import ResolverManager
from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.source import SourceManager

from .pagination import page_after, page_result

DiscoveryCollection = typing.Literal[
  "resolvers", "source_types", "storage_types", "storages"
]


async def discover(
  collection: DiscoveryCollection, *, limit: int, cursor: str | None
) -> dict[str, typing.Any]:
  if not 1 <= limit <= 100:
    raise ValueError("Discovery limit must be between 1 and 100")
  scope = {"tool": "meta_discover", "collection": collection}
  after = page_after(cursor, scope, numeric=collection == "storages")
  rows: list[dict[str, typing.Any]]
  if collection == "resolvers":
    # Slice identities before introspection; no Resolver construction or behavior grant.
    ids = sorted(ResolverManager.RESOLVER_CLS)
    ids = [key for key in ids if after is None or key > typing.cast(str, after)][
      : limit + 1
    ]
    rows = [
      {
        "id": key,
        "description": (inspect.getdoc(ResolverManager.RESOLVER_CLS[key]) or "")[:512],
        "registered_locally": True,
      }
      for key in ids
    ]
  elif collection == "source_types":
    rows = await SourceManager.discover_types(
      limit=limit, after=typing.cast(str | None, after)
    )
    for row in rows:
      row["registered_locally"] = SourceManager.has_source_type(row["id"])
  elif collection == "storage_types":
    rows = await StorageManager.discover_types(
      limit=limit, after=typing.cast(str | None, after)
    )
    for row in rows:
      row["registered_locally"] = row["id"] in StorageManager._STORAGE_CLASSES
  else:
    rows = await StorageManager.discover_instances(
      limit=limit, after=typing.cast(int | None, after)
    )
  return {"collection": collection, **page_result(rows, limit=limit, scope=scope)}
