"""Owner-coherent Agent read tools and exact Organization mutation tools."""

from __future__ import annotations

import typing

import pydantic

from app.business.agent import AgentManager, ToolExecutionError
from app.business.info_base import InfoBaseManager
from app.business.info_base.commands import submit_graph as persist_submitted_graph
from app.business.info_base.resolver import (
  ResolverDraftCapability,
  ResolverManager,
)
from app.schemas.ai import JSONValue
from app.schemas.organization import (
  DraftGraphInput,
  GetDraftGraphSchemaInput,
  SubmitGraphInput,
)
from app.schemas.organization_behavior import (
  DuplicateAssertionProposal,
  EvidenceStanceProposal,
  ExistingReferentAnchorProposal,
  RecordOrganizationCandidateInput,
  RefinementProposal,
  SupersessionProposal,
  SynthesisProposal,
)
from app.schemas.organization_extraction import ExtractionProposal, ExtractionRequest
from ._shared import behavior_resolver_classes, get_behavior_resolver
from .contracts import OrganizationError
from .duplicate_assertion import (
  DUPLICATES_ASSERTION_RELATION,
  DuplicateAssertionBehaviorResolver,
)
from .evidence_stance import EvidenceStanceBehaviorResolver
from .extraction import ExtractionBehaviorResolver
from .referent_anchoring import ExistingReferentAnchoringBehaviorResolver
from .refinement import REFINES_RELATION, RefinementBehaviorResolver
from .supersession import SUPERSEDES_RELATION, SupersessionBehaviorResolver
from .synthesis import SynthesisBehaviorResolver


GET_DRAFT_GRAPH_SCHEMA_TOOL = "get_draft_graph_schema"
DRAFT_GRAPH_TOOL = "draft_graph"
SUBMIT_GRAPH_TOOL = "submit_graph"
RECORD_SUPERSESSION_TOOL = "record_supersession"
RECORD_REFINEMENT_TOOL = "record_refinement"
RECORD_EVIDENCE_STANCE_TOOL = "record_evidence_stance"
CREATE_SYNTHESIS_TOOL = "create_synthesis"
READ_EXTRACTION_MATERIAL_TOOL = "read_extraction_material"
CREATE_EXTRACTION_TOOL = "create_extraction"
ANCHOR_EXISTING_REFERENT_TOOL = "anchor_existing_referent"
RECORD_DUPLICATE_ASSERTION_TOOL = "record_duplicate_assertion"
RECORD_ORGANIZATION_CANDIDATE_TOOL = "record_organization_candidate"


def _draft_capability_snapshot() -> dict[str, ResolverDraftCapability]:
  return {
    capability.resolver: capability
    for capability in ResolverManager.get_draft_capabilities()
  }


def _schema_discovery_input_model() -> type[pydantic.BaseModel]:
  snapshot = _draft_capability_snapshot()
  if not snapshot:  # pragma: no cover - core.text.v1 is always registered
    raise RuntimeError("No Resolver graph-drafting capability is registered")

  def add_exact_ids(schema: dict[str, typing.Any]) -> None:
    schema["properties"]["resolver_types"]["items"] = {
      "type": "string",
      "enum": list(snapshot),
    }

  class BoundGetDraftGraphSchemaInput(GetDraftGraphSchemaInput):
    model_config = pydantic.ConfigDict(
      extra="forbid",
      frozen=True,
      json_schema_extra=add_exact_ids,
    )

    @pydantic.field_validator("resolver_types")
    @classmethod
    def exact_resolvers(cls, resolvers: tuple[str, ...]) -> tuple[str, ...]:
      unknown = tuple(resolver for resolver in resolvers if resolver not in snapshot)
      if unknown:
        raise ValueError(f"Unavailable draft Resolver IDs: {unknown!r}")
      return resolvers

  return BoundGetDraftGraphSchemaInput


def _draft_graph_input_model() -> type[pydantic.BaseModel]:
  snapshot = _draft_capability_snapshot()
  if not snapshot:  # pragma: no cover - core.text.v1 is always registered
    raise RuntimeError("No Resolver graph-drafting capability is registered")

  def add_exact_ids(schema: dict[str, typing.Any]) -> None:
    schema["properties"]["resolver_type"] = {
      "type": "string",
      "enum": list(snapshot),
    }

  class BoundDraftGraphInput(DraftGraphInput):
    model_config = pydantic.ConfigDict(
      extra="forbid",
      json_schema_extra=add_exact_ids,
    )

    @pydantic.field_validator("resolver_type")
    @classmethod
    def exact_resolver(cls, resolver: str) -> str:
      if resolver not in snapshot:
        raise ValueError(f"Unavailable draft Resolver ID: {resolver!r}")
      return resolver

    @pydantic.model_validator(mode="after")
    def validate_resolver_input(self) -> typing.Self:
      capability = snapshot[self.resolver_type]
      # Validate at the caller's actual path; an inner resolver_type error must
      # not tell the Agent to remove its valid outer selector.
      payload_model = pydantic.create_model(
        "ResolverDraftPayload", input=(capability.input_model, ...)
      )
      payload = payload_model.model_validate({"input": self.input})
      object.__setattr__(self, "_resolver_input", getattr(payload, "input"))
      return self

  return BoundDraftGraphInput


@AgentManager.tool(
  GET_DRAFT_GRAPH_SCHEMA_TOOL,
  description="Describe graph-drafting inputs for selected Resolver types.",
  input_model_factory=_schema_discovery_input_model,
)
async def get_draft_graph_schema(input: GetDraftGraphSchemaInput) -> JSONValue:
  snapshot = _draft_capability_snapshot()
  return {
    "resolvers": [
      {
        "resolver_type": resolver,
        "description": snapshot[resolver].description,
        "input_schema": typing.cast(
          dict[str, JSONValue],
          snapshot[resolver].input_model.model_json_schema(),
        ),
      }
      for resolver in input.resolver_types
    ]
  }


@AgentManager.tool(
  DRAFT_GRAPH_TOOL,
  description="Draft one rooted GraphForm through an exact Resolver without persistence.",
  input_model_factory=_draft_graph_input_model,
)
async def draft_graph(input: DraftGraphInput) -> JSONValue:
  capability = ResolverManager.get_draft_capability(input.resolver_type)
  resolver_input = typing.cast(pydantic.BaseModel, getattr(input, "_resolver_input"))
  stars = capability.resolver_cls.create_graph(resolver_input)
  graph = InfoBaseManager.normalize_graph(stars, input.local_block_id_start)
  return typing.cast(JSONValue, graph.model_dump(mode="json"))


@AgentManager.tool(
  SUBMIT_GRAPH_TOOL,
  description="Persist one complete GraphForm and return local-to-persisted Block IDs.",
)
async def submit_graph(input: SubmitGraphInput) -> JSONValue:
  result = await persist_submitted_graph(input.graph)
  return typing.cast(JSONValue, result.model_dump(mode="json"))


def _candidate_input_model() -> type[pydantic.BaseModel]:
  snapshot = {behavior.__rsotype__: behavior for behavior in behavior_resolver_classes()}
  if not snapshot:
    raise RuntimeError("No Organization Behavior Resolver is registered")

  def add_exact_behaviors(schema: dict[str, typing.Any]) -> None:
    schema["properties"]["behavior"] = {
      "oneOf": [
        {
          "const": behavior_id,
          "description": behavior.organization_description,
        }
        for behavior_id, behavior in snapshot.items()
      ]
    }

  class BoundRecordOrganizationCandidateInput(RecordOrganizationCandidateInput):
    model_config = pydantic.ConfigDict(
      extra="forbid",
      frozen=True,
      json_schema_extra=add_exact_behaviors,
    )

    @pydantic.field_validator("behavior")
    @classmethod
    def exact_behavior(cls, behavior: str) -> str:
      if behavior not in snapshot:
        raise ValueError(
          f"Unavailable behavior {behavior!r}; available: {', '.join(snapshot)}"
        )
      return behavior

  return BoundRecordOrganizationCandidateInput


async def _exact_result(operation: typing.Awaitable[pydantic.BaseModel]) -> JSONValue:
  try:
    result = await operation
  except (ValueError, OrganizationError) as error:
    raise ToolExecutionError(
      {"error": type(error).__name__, "message": str(error)}
    ) from error
  return typing.cast(JSONValue, result.model_dump(mode="json"))


@AgentManager.tool(
  RECORD_SUPERSESSION_TOOL,
  description=(
    f"Record {SUPERSEDES_RELATION!r}: the successor is authorized to replace "
    "the predecessor across its entire scope on the same subject."
  ),
)
async def record_supersession(input: SupersessionProposal) -> JSONValue:
  return await _exact_result(
    SupersessionBehaviorResolver.record_supersession(
      input.successor_block_id,
      input.predecessor_block_id,
    )
  )


@AgentManager.tool(
  RECORD_REFINEMENT_TOOL,
  description=(
    f"Record {REFINES_RELATION!r}: new compatible detail at equal or narrower scope "
    "on the same subject, not mere extraction or rewording. The predecessor "
    "remains independently usable as a coarser description."
  ),
)
async def record_refinement(input: RefinementProposal) -> JSONValue:
  return await _exact_result(
    RefinementBehaviorResolver.record_refinement(
      input.refinement_block_id,
      input.predecessor_block_id,
    )
  )


@AgentManager.tool(
  RECORD_EVIDENCE_STANCE_TOOL,
  description=(
    "Record attributable evidence supporting or challenging a whole "
    "assertion in comparable scope, without declaring it true or false. "
    "Merely establishing that derived content faithfully restates its source is "
    "not evidence stance, regardless of source authority. Shared provenance is "
    "allowed when observation or reasoning "
    "contributes reasons beyond restatement."
  ),
)
async def record_evidence_stance(input: EvidenceStanceProposal) -> JSONValue:
  return await _exact_result(
    EvidenceStanceBehaviorResolver.record_evidence_stance(
      input.evidence_block_id,
      input.assertion_block_id,
      input.stance,
    )
  )


@AgentManager.tool(
  CREATE_SYNTHESIS_TOOL,
  description=(
    "Create reusable multi-source information preserving provenance, "
    "disagreement, uncertainty and speaker attribution; copies do not "
    "multiply corroboration."
  ),
)
async def create_synthesis(input: SynthesisProposal) -> JSONValue:
  return await _exact_result(
    SynthesisBehaviorResolver.create_synthesis(
      input.text,
      input.source_block_ids,
      input.previous_synthesis_block_id,
    )
  )


@AgentManager.tool(
  READ_EXTRACTION_MATERIAL_TOOL,
  description=(
    "Read material and direct relations, retaining this run's exact text for extraction."
  ),
)
async def read_extraction_material(input: ExtractionRequest) -> JSONValue:
  return await _exact_result(ExtractionBehaviorResolver.read_material(input.block_id))


@AgentManager.tool(
  CREATE_EXTRACTION_TOOL,
  description=(
    "Save independently useful information with exact excerpts from this run's reads. "
    "Preserve attribution, context and uncertainty. Reuse sufficient existing information; "
    "do not create copies merely to finish."
  ),
)
async def create_extraction(input: ExtractionProposal) -> JSONValue:
  return await _exact_result(ExtractionBehaviorResolver.create_extraction(input))


@AgentManager.tool(
  ANCHOR_EXISTING_REFERENT_TOOL,
  description=(
    "Link a source's referring fragment to an existing identity-bearing "
    "referent, without creating a new referent."
  ),
)
async def anchor_existing_referent(
  input: ExistingReferentAnchorProposal,
) -> JSONValue:
  return await _exact_result(
    ExistingReferentAnchoringBehaviorResolver.anchor_existing_referent(
      input.source_block_id,
      input.selected_text,
      input.referent_block_id,
    )
  )


@AgentManager.tool(
  RECORD_DUPLICATE_ASSERTION_TOOL,
  description=(
    f"Record {DUPLICATES_ASSERTION_RELATION!r}: whole assertions from the same "
    "provenance occurrence with no "
    "independent evidence, reasoning, decision or material gain; matching "
    "words alone are insufficient."
  ),
)
async def record_duplicate_assertion(input: DuplicateAssertionProposal) -> JSONValue:
  return await _exact_result(
    DuplicateAssertionBehaviorResolver.record_duplicate_assertion(
      input.left_block_id,
      input.right_block_id,
    )
  )


@AgentManager.tool(
  RECORD_ORGANIZATION_CANDIDATE_TOOL,
  description="Mark an organization candidate without executing the behavior.",
  input_model_factory=_candidate_input_model,
)
async def record_organization_candidate(
  input: RecordOrganizationCandidateInput,
) -> JSONValue:
  behavior = get_behavior_resolver(input.behavior)
  if behavior is None:
    raise ToolExecutionError({"error": "behavior_unavailable"})
  return await _exact_result(
    typing.cast(typing.Any, behavior).record_candidate(input.block_id)
  )
