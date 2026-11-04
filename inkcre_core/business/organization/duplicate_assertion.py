"""Provenance-aware duplicate assertion behavior."""

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
  record_candidate,
  relation_result,
  require_distinct_blocks,
  run_configured_agent,
)


LOGGER = get_logger().getChild(__name__)

DUPLICATES_ASSERTION_RELATION = "duplicates assertion"
DUPLICATE_ASSERTION_BEHAVIOR = "core.organization.behavior.duplicate-assertion.v1"
DUPLICATE_ASSERTION_CONFIG_KEY = "core.organization.duplicate_assertion"
DUPLICATE_ASSERTION_CONFIG_SCHEMA = "core.organization.duplicate_assertion.config.v1"

DeploymentConfigManager.register_schema(
  DUPLICATE_ASSERTION_CONFIG_SCHEMA,
  BehaviorAgentConfig,
  keys=(DUPLICATE_ASSERTION_CONFIG_KEY,),
)


class DuplicateAssertionBehaviorResolver(
  Resolver[str, str],
  rso_type=DUPLICATE_ASSERTION_BEHAVIOR,
):
  organization_description = (
    "Relate whole-Block assertions copied from the same provenance occurrence."
  )
  judgment_contract = (
    "Both Blocks completely and independently address the compared assertion.",
    "Referent, predicate, polarity, force, units, and material qualifiers match.",
    "Applicable scope, time, version, environment, and attribution are compatible.",
    "Both assertions ultimately derive from the same observable provenance occurrence.",
    "Neither Block adds independent evidence, reasoning, or authoritative decision.",
    "No material asymmetric information gain is hidden by the relation.",
    "The relation prevents evidence multiplication or restores useful provenance paths.",
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
    return "organization behavior: duplicate assertion"

  @classmethod
  async def record_candidate(
    cls,
    block_id: BlockID,
  ) -> CandidateWriteResult:
    return await record_candidate(cls, block_id)

  @classmethod
  async def record_duplicate_assertion(
    cls,
    left_block_id: BlockID,
    right_block_id: BlockID,
  ) -> RelationWriteResult:
    async with graph_uow() as uow:
      await require_distinct_blocks(left_block_id, right_block_id, uow)
      from_, to_ = sorted((left_block_id, right_block_id))
      relation, created = await fetchsert_relation(
        from_,
        to_,
        DUPLICATES_ASSERTION_RELATION,
        uow,
      )
      return relation_result(relation, created)

  @classmethod
  async def can_run_automatic(cls) -> bool:
    return await configured_agent_available(
      DUPLICATE_ASSERTION_CONFIG_KEY,
      BehaviorAgentConfig,
    )

  @classmethod
  async def run_automatic(cls, max_seeds: int) -> None:
    candidates = await candidate_seed_ids(cls, max_seeds)
    recent = await recent_block_ids(max_seeds)
    random = await random_block_ids(max_seeds)
    seeds = merge_seed_categories(max_seeds, candidates, recent, random)
    LOGGER.info(
      "organization.seeds.selected",
      extra={
        "behavior": cls.__rsotype__,
        "max_seeds": max_seeds,
        "candidate_count": len(candidates),
        "recent_count": len(recent),
        "random_count": len(random),
        "seed_block_ids": seeds,
      },
    )
    for seed in seeds:
      with continue_after_seed_failure(LOGGER, cls.__rsotype__, seed):
        message = await build_seed_message(
          "Record only whole-Block duplicate assertions "
          "from the same provenance occurrence.",
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
          DUPLICATE_ASSERTION_CONFIG_KEY,
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
