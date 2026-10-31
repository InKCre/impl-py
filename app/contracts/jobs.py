"""Built-in Job input and catalog facts, without runtime or ORM imports.

Domain handlers retain admission lookups, execution eligibility and effects. This
module owns only their shared input shape and published catalog metadata.
"""

from dataclasses import dataclass
import typing

import pydantic


class SourceCollectParameters(pydantic.BaseModel):
  """Parameters of the exact ordinary Source Job type."""

  model_config = pydantic.ConfigDict(extra="forbid")

  source: int
  config: dict[str, typing.Any] = pydantic.Field(default_factory=dict)


class SourceBackfillParameters(SourceCollectParameters):
  """Parameters of the exact historical Source Job type."""


class LexicalMaintenanceOptions(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  max_records: int = pydantic.Field(default=100, ge=1)
  scan_page_size: int = pydantic.Field(default=100, ge=1)
  diagnostic_limit: int = pydantic.Field(default=20, ge=0)


class LexicalMaintenanceJobParameters(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  options: LexicalMaintenanceOptions = pydantic.Field(
    default_factory=LexicalMaintenanceOptions
  )


class EmbeddingMaintenanceOptions(pydantic.BaseModel):
  """Peer-local bounds for one resumable maintenance invocation."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  max_embeddings: int = pydantic.Field(default=100, ge=1)
  batch_size: int = pydantic.Field(default=20, ge=1)
  scan_page_size: int = pydantic.Field(default=100, ge=1)
  diagnostic_limit: int = pydantic.Field(default=20, ge=0)


class EmbeddingMaintenanceJobParameters(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  profile: int | None = None
  options: EmbeddingMaintenanceOptions = pydantic.Field(
    default_factory=EmbeddingMaintenanceOptions
  )


class MediaInterpretationJobParameters(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)


class RuminationRequest(pydantic.BaseModel):
  """Explicit request to reconsider one focal Block."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  block: int


class AutomaticOrganizationJobParameters(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  max_seeds: int = pydantic.Field(default=10, ge=3, le=100)


class AgentQueryJobParameters(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  sink: int
  query: str = pydantic.Field(min_length=1)


@dataclass(frozen=True)
class JobContract:
  id: str
  description: str
  parameters_model: type[pydantic.BaseModel]
  default_timeout_seconds: int

  @property
  def parameters_schema(self) -> dict[str, typing.Any]:
    return self.parameters_model.model_json_schema()


SOURCE_BACKFILL_JOB_TYPE = "core.source.backfill.v1"
SOURCE_COLLECT_JOB_TYPE = "core.source.collect.v1"
LEXICAL_REBUILD_JOB_TYPE = "core.feature_retrieval.lexical.rebuild.v1"
LEXICAL_MAINTAIN_JOB_TYPE = "core.feature_retrieval.lexical.maintain.v1"
SEMANTIC_REBUILD_JOB_TYPE = "core.semantic_retrieval.rebuild.v1"
SEMANTIC_MAINTAIN_JOB_TYPE = "core.semantic_retrieval.maintain.v1"
DUPLICATE_ASSERTION_JOB_TYPE = "core.organization.duplicate-assertion.automatic.v1"
REFERENT_ANCHORING_JOB_TYPE = "core.organization.existing-referent-anchoring.automatic.v1"
SYNTHESIS_JOB_TYPE = "core.organization.synthesis.automatic.v1"
EVIDENCE_STANCE_JOB_TYPE = "core.organization.evidence-stance.automatic.v1"
REFINEMENT_JOB_TYPE = "core.organization.refinement.automatic.v1"
SUPERSESSION_JOB_TYPE = "core.organization.supersession.automatic.v1"
EXPLICIT_RUMINATION_JOB_TYPE = "core.organization.rumination.explicit.v1"
RUMINATION_JOB_TYPE = "core.organization.rumination.automatic.v1"
AGENT_QUERY_JOB_TYPE = "core.sink.agent-query.v1"
MEDIA_INTERPRETATION_JOB_TYPE = "core.organization.media_interpretation.v1"

BUILTIN_JOB_TYPES = (
  JobContract(
    SOURCE_COLLECT_JOB_TYPE,
    "Run one ordinary collection command for a configured Source.",
    SourceCollectParameters,
    300,
  ),
  JobContract(
    SOURCE_BACKFILL_JOB_TYPE,
    "Run one exact historical collection command for a configured Source.",
    SourceBackfillParameters,
    1800,
  ),
  JobContract(
    LEXICAL_MAINTAIN_JOB_TYPE,
    "Maintain missing or stale Block lexical projection records.",
    LexicalMaintenanceJobParameters,
    900,
  ),
  JobContract(
    LEXICAL_REBUILD_JOB_TYPE,
    "Rebuild Block lexical records present before invocation.",
    LexicalMaintenanceJobParameters,
    1800,
  ),
  JobContract(
    SEMANTIC_MAINTAIN_JOB_TYPE,
    "Maintain missing or stale semantic embedding records.",
    EmbeddingMaintenanceJobParameters,
    900,
  ),
  JobContract(
    SEMANTIC_REBUILD_JOB_TYPE,
    "Rebuild semantic embedding records present before invocation.",
    EmbeddingMaintenanceJobParameters,
    1800,
  ),
  JobContract(
    MEDIA_INTERPRETATION_JOB_TYPE,
    "Interpret missing image, audio, and video Blocks through configured Agents.",
    MediaInterpretationJobParameters,
    1800,
  ),
  JobContract(
    EXPLICIT_RUMINATION_JOB_TYPE,
    "Reconsider one explicitly selected Block through rumination.",
    RuminationRequest,
    1800,
  ),
  JobContract(
    RUMINATION_JOB_TYPE,
    "Automatically reconsider bounded information seeds through rumination.",
    AutomaticOrganizationJobParameters,
    1800,
  ),
  JobContract(
    SUPERSESSION_JOB_TYPE,
    "Automatically judge bounded scoped-supersession candidates.",
    AutomaticOrganizationJobParameters,
    1800,
  ),
  JobContract(
    REFINEMENT_JOB_TYPE,
    "Automatically judge bounded non-dominating refinement candidates.",
    AutomaticOrganizationJobParameters,
    1800,
  ),
  JobContract(
    EVIDENCE_STANCE_JOB_TYPE,
    "Automatically judge bounded attributable evidence stances.",
    AutomaticOrganizationJobParameters,
    1800,
  ),
  JobContract(
    SYNTHESIS_JOB_TYPE,
    "Automatically create bounded provenance-preserving syntheses.",
    AutomaticOrganizationJobParameters,
    1800,
  ),
  JobContract(
    REFERENT_ANCHORING_JOB_TYPE,
    "Automatically anchor bounded mentions to existing referents.",
    AutomaticOrganizationJobParameters,
    1800,
  ),
  JobContract(
    DUPLICATE_ASSERTION_JOB_TYPE,
    "Automatically judge bounded provenance-aware duplicate assertions.",
    AutomaticOrganizationJobParameters,
    1800,
  ),
  JobContract(
    AGENT_QUERY_JOB_TYPE,
    "Answer one information need through an enabled Agent Query Sink.",
    AgentQueryJobParameters,
    300,
  ),
)

BUILTIN_JOB_TYPES_BY_ID = {item.id: item for item in BUILTIN_JOB_TYPES}
