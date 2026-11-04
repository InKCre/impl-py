"""Agent Tool controllers for graph-navigation retrieval."""

import typing

import pydantic

from inkcre_core.business.agent import AgentManager, ToolExecutionError
from inkcre_core.business.agent.projection import project_json
from inkcre_core.schemas.ai import JSONValue
from inkcre_core.schemas.graph_navigation_retrieval import (
  DEFAULT_MAX_EXPLORED_BLOCKS,
  DEFAULT_MAX_EXPLORED_RELATIONS,
  DEFAULT_MAX_HOPS,
  DEFAULT_NEIGHBORHOOD_LIMIT,
  MAX_MAX_EXPLORED_BLOCKS,
  MAX_MAX_HOPS,
  MAX_NEIGHBORHOOD_LIMIT,
  GraphDirection,
)
from inkcre_core.schemas.info_base.block import BlockID
from inkcre_core.schemas.info_base.relation import RelationID

from .main import GraphNavigationRetrievalManager


GET_ENTITY_NEIGHBORHOOD_TOOL = "get_entity_neighborhood"
FIND_PATH_TOOL = "find_path"
GET_CONNECTED_COMPONENTS_TOOL = "get_connected_components"


class BlockNeighborhoodInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  entity_type: typing.Literal["block"]
  entity_id: BlockID
  direction: GraphDirection = "both"
  contents: tuple[str, ...] = ()
  limit: int = pydantic.Field(
    default=DEFAULT_NEIGHBORHOOD_LIMIT, ge=1, le=MAX_NEIGHBORHOOD_LIMIT
  )
  cursor: RelationID | None = None


class RelationNeighborhoodInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  entity_type: typing.Literal["relation"]
  entity_id: RelationID


class EntityNeighborhoodInput(
  pydantic.RootModel[
    typing.Annotated[
      BlockNeighborhoodInput | RelationNeighborhoodInput,
      pydantic.Field(discriminator="entity_type"),
    ]
  ]
):
  model_config = pydantic.ConfigDict(json_schema_extra={"type": "object"})

  @classmethod
  def model_json_schema(cls, *args, **kwargs) -> dict[str, typing.Any]:
    schema = super().model_json_schema(*args, **kwargs)
    properties = BlockNeighborhoodInput.model_json_schema(*args, **kwargs)["properties"]
    properties["entity_type"] = {"type": "string", "enum": ["block", "relation"]}
    schema["properties"] = properties
    return schema


class FindPathInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  from_block_id: BlockID
  to_block_id: BlockID
  direction: GraphDirection = "both"
  contents: tuple[str, ...] = ()
  max_hops: int = pydantic.Field(default=DEFAULT_MAX_HOPS, ge=0, le=MAX_MAX_HOPS)
  max_explored_blocks: int = pydantic.Field(
    default=DEFAULT_MAX_EXPLORED_BLOCKS, ge=1, le=MAX_MAX_EXPLORED_BLOCKS
  )


class ConnectedComponentsInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  seed_block_ids: tuple[BlockID, ...]
  contents: tuple[str, ...] = pydantic.Field(min_length=1)
  max_explored_blocks: int = pydantic.Field(default=DEFAULT_MAX_EXPLORED_BLOCKS, ge=1)
  max_explored_relations: int = pydantic.Field(default=DEFAULT_MAX_EXPLORED_RELATIONS, ge=1)


@AgentManager.tool(
  GET_ENTITY_NEIGHBORHOOD_TOOL,
  description=(
    "Read a Block's direct neighborhood or a Relation with its endpoints. "
    "Null may indicate an incorrect entity type."
  ),
)
async def get_entity_neighborhood(input: EntityNeighborhoodInput) -> JSONValue:
  request = input.root
  if request.entity_type == "block":
    result = await GraphNavigationRetrievalManager.get_block_neighborhood(
      request.entity_id,
      direction=request.direction,
      contents=request.contents,
      limit=request.limit,
      cursor=request.cursor,
    )
  else:
    result = await GraphNavigationRetrievalManager.get_relation_neighborhood(
      request.entity_id
    )
  return project_json(result)


@AgentManager.tool(
  FIND_PATH_TOOL,
  description="Find a bounded graph path; an exploration limit is not proof of absence.",
)
async def find_path(input: FindPathInput) -> JSONValue:
  return project_json(
    await GraphNavigationRetrievalManager.find_path(
      input.from_block_id,
      input.to_block_id,
      direction=input.direction,
      contents=input.contents,
      max_hops=input.max_hops,
      max_explored_blocks=input.max_explored_blocks,
    )
  )


@AgentManager.tool(
  GET_CONNECTED_COMPONENTS_TOOL,
  description=(
    "Partition seeds by bounded undirected reachability through exact Relation contents."
  ),
)
async def get_connected_components(input: ConnectedComponentsInput) -> JSONValue:
  try:
    result = await GraphNavigationRetrievalManager.get_connected_components(
      **input.model_dump()
    )
  except ValueError as error:
    raise ToolExecutionError(
      {"error": type(error).__name__, "message": str(error)}
    ) from error
  return project_json(result)
