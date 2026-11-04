"""Non-dominating refinement relation and automatic semantic judgment."""

from __future__ import annotations

import typing


from inkcre_core.business.deployment_config import DeploymentConfigManager
from inkcre_core.business.info_base.resolver import Resolver
from inkcre_core.persistence.info_base.uow import GraphUnitOfWork, graph_uow
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
from .supersession import EDITED_RELATION


LOGGER = get_logger().getChild(__name__)

REFINES_RELATION = "refines"
REFINEMENT_BEHAVIOR = "core.organization.behavior.refinement.v1"
REFINEMENT_CONFIG_KEY = "core.organization.refinement"
REFINEMENT_CONFIG_SCHEMA = "core.organization.refinement.config.v1"

DeploymentConfigManager.register_schema(
  REFINEMENT_CONFIG_SCHEMA, BehaviorAgentConfig, keys=(REFINEMENT_CONFIG_KEY,)
)


class RefinementBehaviorResolver(
  Resolver[str, str],
  rso_type=REFINEMENT_BEHAVIOR,
):
  organization_description = (
    "Relate useful compatible detail that refines but does not replace information."
  )
  judgment_contract = (
    "Both endpoints are complete addressable information units.",
    "They continue the same referent and evolvable subject.",
    "The refinement scope equals or is visibly contained by the predecessor scope.",
    "Their information roles and attribution remain compatible.",
    "The refinement adds reusable detail, constraints, explanation, or precision.",
    "The predecessor remains independently safe as a coarser description.",
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
    return "organization behavior: refinement"

  @classmethod
  async def record_candidate(
    cls,
    block_id: BlockID,
  ) -> CandidateWriteResult:
    return await record_candidate(cls, block_id)

  @classmethod
  async def record_refinement(
    cls,
    refinement_block_id: BlockID,
    predecessor_block_id: BlockID,
  ) -> RelationWriteResult:
    async with graph_uow() as uow:
      await require_distinct_blocks(refinement_block_id, predecessor_block_id, uow)
      if await cls._has_directed_path(
        predecessor_block_id,
        refinement_block_id,
        uow=uow,
      ):
        raise ValueError(
          f"Cannot refine: an existing refines path runs from predecessor "
          f"{predecessor_block_id} to refinement {refinement_block_id}"
        )
      relation, created = await fetchsert_relation(
        refinement_block_id,
        predecessor_block_id,
        REFINES_RELATION,
        uow,
      )
      return relation_result(relation, created)

  @classmethod
  async def _has_directed_path(
    cls,
    start: BlockID,
    target: BlockID,
    *,
    uow: GraphUnitOfWork,
  ) -> bool:
    frontier = {start}
    visited = {start}
    while frontier:
      rows = tuple(
        relation.to_
        for relation in await uow.relations.get_outgoing_many(
          frontier, content=REFINES_RELATION
        )
      )
      next_frontier = set(rows) - visited
      if target in next_frontier:
        return True
      visited.update(next_frontier)
      frontier = next_frontier
    return False

  @classmethod
  async def can_run_automatic(cls) -> bool:
    return await configured_agent_available(REFINEMENT_CONFIG_KEY, BehaviorAgentConfig)

  @classmethod
  async def run_automatic(cls, max_seeds: int) -> None:
    candidates = await candidate_seed_ids(cls, max_seeds)
    strong = merge_seed_categories(
      max_seeds,
      await recent_relation_endpoint_ids(max_seeds, contents=(EDITED_RELATION,)),
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
          "Determine only useful non-dominating refinement relations.",
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
        await run_configured_agent(REFINEMENT_CONFIG_KEY, BehaviorAgentConfig, message)
        LOGGER.info(
          "organization.seed.considered",
          extra={
            "behavior": cls.__rsotype__,
            "seed_block_ids": (seed,),
            "outcome": "considered",
            "reason": "agent_completed",
          },
        )
