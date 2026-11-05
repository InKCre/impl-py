"""Dynamic ordinary Resolver method discovery and invocation."""

import typing

import fastapi
from fastapi.exceptions import RequestValidationError

from inkcre_core.business.info_base.services import BlockService
from inkcre_core.business.info_base.resolver import (
  ResolverManager,
  ResolverMethodInputError,
  UnknownResolverError,
  UnknownResolverMethodError,
  UnsupportedResolverCapability,
)

from .content import CONTENT_RESPONSES, content_response


ROUTER = fastapi.APIRouter(tags=["resolver"])


@ROUTER.get("/resolvers")
def list_resolvers(
  limit: int | None = fastapi.Query(None, gt=0), cursor: str | None = None
) -> dict[str, typing.Any]:
  ids = sorted(
    key for key in ResolverManager.RESOLVER_CLS if cursor is None or key > cursor
  )
  more = limit is not None and len(ids) > limit
  ids = ids[:limit]
  return {
    "resolvers": [
      {"id": key, "description": ResolverManager.RESOLVER_CLS[key].__doc__ or ""}
      for key in ids
    ],
    "next_cursor": ids[-1] if more else None,
  }


def _methods(resolver_id: str, limit: int | None, cursor: str | None) -> dict:
  if resolver_id not in ResolverManager.RESOLVER_CLS:
    raise fastapi.HTTPException(404, f"Resolver {resolver_id!r} not registered")
  methods = sorted(
    (
      item
      for item in ResolverManager.get_method_contracts(resolver_id)
      if cursor is None or item.name > cursor
    ),
    key=lambda item: item.name,
  )
  more = limit is not None and len(methods) > limit
  methods = methods[:limit]
  return {
    "resolver": resolver_id,
    "methods": [
      {
        "name": item.name,
        "description": item.description,
        "input_schema": item.input_schema,
      }
      for item in methods
    ],
    "next_cursor": methods[-1].name if more else None,
  }


@ROUTER.get("/resolvers/{resolver_id}/methods")
def resolver_methods(
  resolver_id: str,
  limit: int | None = fastapi.Query(None, gt=0),
  cursor: str | None = None,
) -> dict:
  return _methods(resolver_id, limit, cursor)


@ROUTER.get("/blocks/{block_id}/resolver/methods")
async def block_resolver_methods(
  block_id: int,
  limit: int | None = fastapi.Query(None, gt=0),
  cursor: str | None = None,
) -> dict:
  block = await BlockService.get(block_id)
  if block is None:
    raise fastapi.HTTPException(404, f"Block {block_id} not found")
  return _methods(block.resolver, limit, cursor)


@ROUTER.post("/blocks/{block_id}/resolver/methods/{method}", responses=CONTENT_RESPONSES)
async def invoke_resolver_method(
  block_id: int, method: str, body: dict = fastapi.Body(default_factory=dict)
) -> fastapi.Response:
  block = await BlockService.get(block_id)
  if block is None:
    raise fastapi.HTTPException(404, f"Block {block_id} not found")
  try:
    value = await ResolverManager.invoke_method(block, method, body)
  except ResolverMethodInputError as error:
    raise RequestValidationError(
      [{**item, "loc": ("body", *item["loc"])} for item in error.errors(include_url=False)]
    ) from error
  except (UnknownResolverError, UnknownResolverMethodError) as error:
    raise fastapi.HTTPException(404, str(error)) from error
  except UnsupportedResolverCapability as error:
    raise fastapi.HTTPException(409, str(error)) from error
  return await content_response(value)
