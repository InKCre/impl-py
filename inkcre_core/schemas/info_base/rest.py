"""Ordinary REST input forms; persisted entities and Peer wire formats stay separate."""

import typing

import pydantic

from inkcre_core.schemas.graph_navigation_retrieval import (
  DEFAULT_MAX_HOPS,
  MAX_MAX_HOPS,
  DEFAULT_MAX_EXPLORED_BLOCKS,
  MAX_MAX_EXPLORED_BLOCKS,
  DEFAULT_MAX_EXPLORED_RELATIONS,
  GraphDirection,
)


class EntityReference(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")
  type: typing.Literal["block", "relation"]
  id: int


class EntitiesGetForm(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")
  entities: list[EntityReference]
  content: typing.Literal["none", "raw", "hydrated"] = "raw"


class BlockUpdateForm(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")
  content: str = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]
  resolver: str = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]
  storage: int | None = None


class RelationUpdateForm(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")
  from_block_id: int = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]
  to_block_id: int = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]
  content: str = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]


class GraphPathForm(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")
  from_block_id: int
  to_block_id: int
  direction: GraphDirection = "both"
  contents: tuple[str, ...] = ()
  max_hops: int = pydantic.Field(DEFAULT_MAX_HOPS, ge=0, le=MAX_MAX_HOPS)
  max_explored_blocks: int = pydantic.Field(
    DEFAULT_MAX_EXPLORED_BLOCKS, ge=1, le=MAX_MAX_EXPLORED_BLOCKS
  )


class GraphComponentsForm(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")
  seed_block_ids: tuple[int, ...]
  contents: tuple[str, ...] = pydantic.Field(min_length=1)
  max_explored_blocks: int = pydantic.Field(DEFAULT_MAX_EXPLORED_BLOCKS, ge=1)
  max_explored_relations: int = pydantic.Field(DEFAULT_MAX_EXPLORED_RELATIONS, ge=1)
