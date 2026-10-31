"""Semantic retrieval configuration, maintenance and ranked result contracts."""

from app.contracts.jobs import (
  EmbeddingMaintenanceOptions as EmbeddingMaintenanceOptions,
  EmbeddingMaintenanceJobParameters as EmbeddingMaintenanceJobParameters,
)

import math
import typing

import pydantic

from app.schemas.ai import EmbeddingProfileID
from app.schemas.info_base.block import BlockModel
from app.schemas.info_base.relation import RelationModel


SemanticEntityType: typing.TypeAlias = typing.Literal["block", "relation"]


def _default_entity_types() -> frozenset[SemanticEntityType]:
  return frozenset(("block", "relation"))


class SemanticRetrievalConfig(pydantic.BaseModel):
  """Deployment-owned selection of the default vector space."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  default_profile: EmbeddingProfileID | None = None


class VectorRetrievalOptions(pydantic.BaseModel):
  """Query-scoped controls over one already compatible vector space."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  limit: int = pydantic.Field(default=20, ge=1, le=100)
  min_score: float | None = pydantic.Field(default=None, ge=-1, le=1)
  entity_types: frozenset[SemanticEntityType] = pydantic.Field(
    default_factory=_default_entity_types,
    min_length=1,
  )

  @pydantic.field_validator("min_score")
  @classmethod
  def finite_min_score(cls, value: float | None) -> float | None:
    if value is not None and not math.isfinite(value):
      raise ValueError("min_score must be finite")
    return value


class SemanticRetrievalRequest(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  query: str
  profile: EmbeddingProfileID | None = None
  options: VectorRetrievalOptions = pydantic.Field(default_factory=VectorRetrievalOptions)

  @pydantic.field_validator("query")
  @classmethod
  def non_empty_query(cls, value: str) -> str:
    if not value.strip():
      raise ValueError("query must not be empty")
    return value


class BlockSemanticRetrievalMatch(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: typing.Literal["block"] = "block"
  entity: BlockModel
  score: float = pydantic.Field(ge=-1, le=1)


class RelationSemanticRetrievalMatch(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: typing.Literal["relation"] = "relation"
  entity: RelationModel
  score: float = pydantic.Field(ge=-1, le=1)


SemanticRetrievalMatch: typing.TypeAlias = typing.Annotated[
  BlockSemanticRetrievalMatch | RelationSemanticRetrievalMatch,
  pydantic.Field(discriminator="type"),
]


class SemanticRetrievalResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  profile: EmbeddingProfileID
  metric: typing.Literal["cosine"] = "cosine"
  matches: tuple[SemanticRetrievalMatch, ...]


class EmbeddingMaintenanceDiagnostic(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  entity_type: SemanticEntityType
  entity: int
  outcome: typing.Literal["unavailable", "failed"]
  reason: str


class EmbeddingMaintenanceReport(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  profile: EmbeddingProfileID
  embedded: int = 0
  unavailable: int = 0
  failed: int = 0
  diagnostics: tuple[EmbeddingMaintenanceDiagnostic, ...] = ()
