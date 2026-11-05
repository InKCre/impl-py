"""Ordinary bounded graph queries; no Resolver, layout or Peer invocation."""

import fastapi
import pydantic

from inkcre_core.business.graph_navigation_retrieval import GraphNavigationRetrievalManager
from inkcre_core.schemas.graph_navigation_retrieval import (
  GraphDirection,
  GraphModel,
  DEFAULT_NEIGHBORHOOD_LIMIT,
  MAX_NEIGHBORHOOD_LIMIT,
)
from inkcre_core.schemas.info_base.rest import GraphPathForm, GraphComponentsForm

from .entities import relation_record


ROUTER = fastapi.APIRouter(tags=["graph-navigation"])


def _result(value: pydantic.BaseModel) -> dict:
  result = value.model_dump(mode="json")
  for field in ("graph", "proof_graph"):
    graph = getattr(value, field, None)
    if isinstance(graph, GraphModel):
      result[field]["relations"] = [relation_record(row) for row in graph.relations]
  return result


@ROUTER.get("/blocks/{block_id}/neighborhood")
async def block_neighborhood(
  block_id: int,
  direction: GraphDirection = "both",
  contents: list[str] = fastapi.Query(default_factory=list),
  limit: int = fastapi.Query(DEFAULT_NEIGHBORHOOD_LIMIT, ge=1, le=MAX_NEIGHBORHOOD_LIMIT),
  cursor: int | None = None,
) -> dict:
  result = await GraphNavigationRetrievalManager.get_block_neighborhood(
    block_id, direction=direction, contents=contents, limit=limit, cursor=cursor
  )
  if result is None:
    raise fastapi.HTTPException(404, f"Block {block_id} not found")
  return _result(result)


@ROUTER.get("/relations/{relation_id}/neighborhood")
async def relation_neighborhood(relation_id: int) -> dict:
  result = await GraphNavigationRetrievalManager.get_relation_neighborhood(relation_id)
  if result is None:
    raise fastapi.HTTPException(404, f"Relation {relation_id} not found")
  return _result(result)


@ROUTER.post("/graph/path")
async def graph_path(body: GraphPathForm) -> dict:
  return _result(
    await GraphNavigationRetrievalManager.find_path(
      body.from_block_id,
      body.to_block_id,
      direction=body.direction,
      contents=body.contents,
      max_hops=body.max_hops,
      max_explored_blocks=body.max_explored_blocks,
    )
  )


@ROUTER.post("/graph/components")
async def graph_components(body: GraphComponentsForm) -> dict:
  return _result(
    await GraphNavigationRetrievalManager.get_connected_components(
      body.seed_block_ids,
      contents=body.contents,
      max_explored_blocks=body.max_explored_blocks,
      max_explored_relations=body.max_explored_relations,
    )
  )
