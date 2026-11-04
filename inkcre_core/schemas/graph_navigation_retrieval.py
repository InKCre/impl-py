"""Presentation-neutral graph-navigation retrieval contracts."""

import typing

import pydantic

from inkcre_core.schemas.info_base.block import BlockID, BlockModel
from inkcre_core.schemas.info_base.relation import RelationID, RelationModel


GraphDirection: typing.TypeAlias = typing.Literal["in", "out", "both"]

DEFAULT_NEIGHBORHOOD_LIMIT = 20
MAX_NEIGHBORHOOD_LIMIT = 100
DEFAULT_MAX_HOPS = 4
MAX_MAX_HOPS = 8
DEFAULT_MAX_EXPLORED_BLOCKS = 1000
MAX_MAX_EXPLORED_BLOCKS = 10000
DEFAULT_MAX_EXPLORED_RELATIONS = 10000


class GraphModel(pydantic.BaseModel):
  """Endpoint-closed persisted graph read model."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  blocks: tuple[BlockModel, ...]
  relations: tuple[RelationModel, ...]


class BlockNeighborhood(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  focal_block: BlockID
  graph: GraphModel
  next_cursor: RelationID | None = None


class RelationNeighborhood(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  focal_relation: RelationID
  graph: GraphModel


class ConnectedSeedComponent(pydantic.BaseModel):
  """One observed undirected component containing one or more input seeds."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  seed_block_ids: tuple[BlockID, ...]
  member_block_ids: tuple[BlockID, ...]


class ConnectedComponentsResult(pydantic.BaseModel):
  """Bounded seed partition plus an endpoint-closed spanning proof."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  components: tuple[ConnectedSeedComponent, ...]
  proof_graph: GraphModel
  missing_seed_block_ids: tuple[BlockID, ...]
  truncated: bool


class PathFound(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  status: typing.Literal["found"] = "found"
  graph: GraphModel
  block_path: tuple[BlockID, ...]
  relation_path: tuple[RelationID, ...]


class PathNotFound(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  status: typing.Literal["not_found"] = "not_found"


class PathLimitReached(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  status: typing.Literal["limit_reached"] = "limit_reached"


PathResult: typing.TypeAlias = typing.Annotated[
  PathFound | PathNotFound | PathLimitReached,
  pydantic.Field(discriminator="status"),
]
