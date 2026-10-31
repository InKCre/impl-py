"""Contracts for exact Organization behaviors and their Agent adapters."""

from app.contracts.jobs import (
  AutomaticOrganizationJobParameters as AutomaticOrganizationJobParameters,
)

import typing

import pydantic

from app.schemas.graph_navigation_retrieval import (
  GraphModel,
)
from app.schemas.info_base.block import BlockID, ResolverType
from app.schemas.info_base.relation import RelationID


class BehaviorAgentConfig(pydantic.BaseModel):
  """Deployment selection of one purpose-built Agent definition."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  agent: int


class RelationWriteResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  relation_id: RelationID
  created: bool


class CandidateWriteResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  descriptor_block_id: BlockID
  relation_id: RelationID
  created: bool


class SupersessionProposal(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  successor_block_id: BlockID
  predecessor_block_id: BlockID


class RefinementProposal(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  refinement_block_id: BlockID
  predecessor_block_id: BlockID


class EvidenceStanceProposal(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  evidence_block_id: BlockID
  assertion_block_id: BlockID
  stance: typing.Literal["supports", "challenges"]


class SynthesisProposal(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  text: str
  source_block_ids: tuple[BlockID, ...] = pydantic.Field(
    min_length=2, description="Actual contributing sources, not all inspected context."
  )
  previous_synthesis_block_id: BlockID | None = pydantic.Field(
    default=None, description="Earlier synthesis revised through an edited relation."
  )

  @pydantic.field_validator("text")
  @classmethod
  def non_empty_text(cls, value: str) -> str:
    if not value.strip():
      raise ValueError("text must not be empty")
    return value

  @pydantic.field_validator("source_block_ids")
  @classmethod
  def distinct_sources(cls, value: tuple[BlockID, ...]) -> tuple[BlockID, ...]:
    if len(set(value)) != len(value):
      raise ValueError("source_block_ids must be distinct")
    return value


class SynthesisWriteResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  synthesis_block_id: BlockID
  synthesis_created: bool
  basis: tuple[RelationWriteResult, ...]
  edited: RelationWriteResult | None = None


class ExistingReferentAnchorProposal(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  source_block_id: BlockID
  selected_text: str = pydantic.Field(
    description="Minimal sufficient referring fragment from the source."
  )
  referent_block_id: BlockID

  @pydantic.field_validator("selected_text")
  @classmethod
  def non_empty_selected_text(cls, value: str) -> str:
    if not value.strip():
      raise ValueError("selected_text must not be empty")
    return value


class ExistingReferentAnchorResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  fragment_block_id: BlockID
  fragment_created: bool
  has_mention: RelationWriteResult | None
  refers_to: RelationWriteResult


class DuplicateAssertionProposal(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  left_block_id: BlockID
  right_block_id: BlockID


class RecordOrganizationCandidateInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  block_id: BlockID
  behavior: ResolverType


class SupersessionLineage(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  graph: GraphModel
  current_block_ids: tuple[BlockID, ...]
  truncated: bool
  cycle_detected: bool
