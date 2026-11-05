"""Provenance-preserving evidence stance relation behavior."""

from __future__ import annotations

import typing


from inkcre_core.business.deployment_config import DeploymentConfigManager
from inkcre_core.business.info_base.resolver import Resolver
from inkcre_core.persistence.info_base.uow import graph_uow
from inkcre_core.libs.obsrv.main import get_logger
from inkcre_core.schemas.info_base.block import BlockID
from inkcre_core.schemas.organization_behavior import (
  BehaviorAgentConfig,
  CandidateWriteResult,
  RelationWriteResult,
)

from ._shared import (
  build_seed_message,
  candidate_seed_ids,
  configured_agent_available,
  continue_after_seed_failure,
  fetchsert_relation,
  merge_seed_categories,
  random_block_ids,
  recent_block_ids,
  recent_relation_endpoint_ids,
  record_candidate,
  relation_result,
  require_distinct_blocks,
  run_configured_agent,
)


LOGGER = get_logger().getChild(__name__)

SUPPORTS_RELATION = "supports"
CHALLENGES_RELATION = "challenges"
EVIDENCE_STANCE_BEHAVIOR = "core.organization.behavior.evidence-stance.v1"
EVIDENCE_STANCE_CONFIG_KEY = "core.organization.evidence_stance"
EVIDENCE_STANCE_CONFIG_SCHEMA = "core.organization.evidence_stance.config.v1"

DeploymentConfigManager.register_schema(
  EVIDENCE_STANCE_CONFIG_SCHEMA,
  BehaviorAgentConfig,
  keys=(EVIDENCE_STANCE_CONFIG_KEY,),
)


class EvidenceStanceBehaviorResolver(
  Resolver[str, str],
  rso_type=EVIDENCE_STANCE_BEHAVIOR,
):
  organization_description = (
    "Relate attributable evidence that supports or challenges an assertion."
  )
  judgment_contract = (
    "Evidence and assertion are complete addressable information units.",
    "The source is evidence and the target is an evaluable assertion.",
    "Their proposition and applicable scope are comparable.",
    "The evidence contributes reasons beyond establishing source fidelity; "
    "source authority alone is insufficient.",
    "Evidence provenance and speaker attribution remain recoverable.",
    "The stance is unambiguously support or challenge for the whole assertion.",
    "Duplicate, refinement, replacement, or topical proximity alone is insufficient.",
  )

  async def get_text(
    self,
    *,
    context: typing.Literal["default", "lexical"] = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del context, refresh, materialize_missing
    return self.organization_description

  async def get_label(self, *, refresh: bool = False) -> str:
    del refresh
    return "organization behavior: evidence stance"

  @classmethod
  async def record_candidate(
    cls,
    block_id: BlockID,
  ) -> CandidateWriteResult:
    return await record_candidate(cls, block_id)

  @classmethod
  async def record_evidence_stance(
    cls,
    evidence_block_id: BlockID,
    assertion_block_id: BlockID,
    stance: typing.Literal["supports", "challenges"],
  ) -> RelationWriteResult:
    async with graph_uow() as uow:
      await require_distinct_blocks(evidence_block_id, assertion_block_id, uow)
      opposite = CHALLENGES_RELATION if stance == SUPPORTS_RELATION else SUPPORTS_RELATION
      existing_opposite = await uow.relations.get_outgoing_many(
        (evidence_block_id,), to_ids=(assertion_block_id,), content=opposite
      )
      if existing_opposite:
        raise ValueError("The evidence/assertion pair already has the opposite stance")
      relation, created = await fetchsert_relation(
        evidence_block_id,
        assertion_block_id,
        stance,
        uow,
      )
      return relation_result(relation, created)

  @classmethod
  async def can_run_automatic(cls) -> bool:
    return await configured_agent_available(EVIDENCE_STANCE_CONFIG_KEY, BehaviorAgentConfig)

  @classmethod
  async def run_automatic(cls, max_seeds: int) -> None:
    candidates = await candidate_seed_ids(cls, max_seeds)
    strong = merge_seed_categories(
      max_seeds,
      await recent_relation_endpoint_ids(max_seeds),
      await recent_block_ids(max_seeds),
    )
    random = await random_block_ids(max_seeds)
    seeds = merge_seed_categories(max_seeds, candidates, strong, random)
    LOGGER.info(
      "organization.seeds.selected",
      extra={
        "behavior": cls.__rsotype__,
        "max_seeds": max_seeds,
        "candidate_count": len(candidates),
        "strong_count": len(strong),
        "random_count": len(random),
        "seed_block_ids": seeds,
      },
    )
    for seed in seeds:
      with continue_after_seed_failure(LOGGER, cls.__rsotype__, seed):
        message = await build_seed_message(
          "Determine only attributable evidence support or challenge relations.",
          cls.judgment_contract,
          seed,
        )
        if message is None:
          LOGGER.info(
            "organization.seed.considered",
            extra={
              "behavior": cls.__rsotype__,
              "seed_block_ids": (seed,),
              "outcome": "unresolved",
              "reason": "text_unavailable",
            },
          )
          continue
        await run_configured_agent(
          EVIDENCE_STANCE_CONFIG_KEY,
          BehaviorAgentConfig,
          message,
        )
        LOGGER.info(
          "organization.seed.considered",
          extra={
            "behavior": cls.__rsotype__,
            "seed_block_ids": (seed,),
            "outcome": "considered",
            "reason": "agent_completed",
          },
        )
