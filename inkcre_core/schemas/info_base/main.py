"""Producer graph forms and graph-command results."""

__all__ = [
  "Vector",
  "OutArcForm",
  "InArcForm",
  "StarsGraphForm",
  "GraphBlockForm",
  "GraphRelationForm",
  "GraphForm",
  "GraphBlockIDMapping",
  "SubmitGraphResult",
]

import typing

import pydantic
import sqlmodel

from inkcre_core.schemas.info_base.block import BlockForm, BlockID
from inkcre_core.schemas.info_base.relation import RelationForm


Vector: typing.TypeAlias = tuple[float, ...]
NegativeBlockID: typing.TypeAlias = typing.Annotated[int, pydantic.Field(lt=0)]
NonZeroBlockID: typing.TypeAlias = int


class OutArcForm(sqlmodel.SQLModel):
  """One outgoing relation from the enclosing star to a recursive star."""

  model_config = {"extra": "forbid"}

  relation: RelationForm
  to_graph: "StarsGraphForm"


class InArcForm(sqlmodel.SQLModel):
  """One incoming relation from a recursive star to the enclosing star."""

  model_config = {"extra": "forbid"}

  relation: RelationForm
  from_graph: "StarsGraphForm"


class StarsGraphForm(sqlmodel.SQLModel):
  """Recursive star-centered authoring form used by Resolvers and extensions."""

  model_config = {"extra": "forbid"}

  block: BlockForm
  out_arcs: tuple[OutArcForm, ...] = ()
  in_arcs: tuple[InArcForm, ...] = ()


class GraphBlockForm(BlockForm):
  """A new Block declaration under one GraphForm-local negative ID."""

  id: NegativeBlockID


class GraphRelationForm(RelationForm):
  """A Relation declaration over the GraphForm signed Block-ID namespace."""

  from_: NonZeroBlockID
  to_: NonZeroBlockID

  @pydantic.field_validator("from_", "to_")
  @classmethod
  def require_non_zero_endpoint(cls, value: int) -> int:
    if value == 0:
      raise ValueError("GraphForm Relation endpoints must be non-zero")
    return value


class GraphForm(sqlmodel.SQLModel):
  """Flat command for adding arbitrarily connected Blocks and Relations."""

  model_config = {"extra": "forbid"}

  blocks: tuple[GraphBlockForm, ...] = ()
  relations: tuple[GraphRelationForm, ...] = ()

  @pydantic.model_validator(mode="after")
  def validate_local_block_references(self) -> "GraphForm":
    local_ids = [block.id for block in self.blocks]
    if len(local_ids) != len(set(local_ids)):
      raise ValueError("GraphForm new Block IDs must be unique")

    unresolved = {
      endpoint
      for relation in self.relations
      for endpoint in (relation.from_, relation.to_)
      if endpoint < 0 and endpoint not in local_ids
    }
    if unresolved:
      rendered = ", ".join(str(value) for value in sorted(unresolved))
      raise ValueError(f"GraphForm contains unresolved local Block IDs: {rendered}")
    return self


class GraphBlockIDMapping(sqlmodel.SQLModel):
  """Map one command-local Block identity to its persisted identity."""

  local_id: NegativeBlockID
  id: BlockID


class SubmitGraphResult(sqlmodel.SQLModel):
  """Minimal successful graph-command result used by producers and Agent Tools."""

  blocks: tuple[GraphBlockIDMapping, ...]


StarsGraphForm.model_rebuild()
