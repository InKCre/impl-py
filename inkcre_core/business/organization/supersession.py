"""Scoped supersession relation and automatic semantic judgment."""

from __future__ import annotations

from collections import deque
from graphlib import CycleError, TopologicalSorter
import typing


from inkcre_core.business.deployment_config import DeploymentConfigManager
from inkcre_core.business.info_base.resolver import Resolver
from inkcre_core.persistence.info_base.uow import GraphUnitOfWork, graph_uow
from inkcre_core.libs.obsrv.main import get_logger
from inkcre_core.schemas.graph_navigation_retrieval import GraphModel
from inkcre_core.schemas.info_base.block import BlockID
from inkcre_core.schemas.info_base.relation import RelationID, RelationModel
from inkcre_core.schemas.organization_behavior import (
  BehaviorAgentConfig,
  CandidateWriteResult,
  RelationWriteResult,
  SupersessionLineage,
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

SUPERSEDES_RELATION = "supersedes"
EDITED_RELATION = "edited"
SUPERSESSION_BEHAVIOR = "core.organization.behavior.supersession.v1"
SUPERSESSION_CONFIG_KEY = "core.organization.supersession"
SUPERSESSION_CONFIG_SCHEMA = "core.organization.supersession.config.v1"

DeploymentConfigManager.register_schema(
  SUPERSESSION_CONFIG_SCHEMA,
  BehaviorAgentConfig,
  keys=(SUPERSESSION_CONFIG_KEY,),
)


class SupersessionBehaviorResolver(
  Resolver[str, str],
  rso_type=SUPERSESSION_BEHAVIOR,
):
  organization_description = (
    "Relate a semantic successor that fully replaces one predecessor in scope."
  )
  judgment_contract = (
    "Both endpoints are complete addressable information units.",
    "They continue the same referent and evolvable subject.",
    "The successor covers the predecessor's complete applicable scope.",
    "Semantic order, not collection time, identifies successor and predecessor.",
    "The successor has authority for this subject and scope.",
    "Continuing to use the predecessor as current would be wrong.",
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
    return "organization behavior: supersession"

  @classmethod
  async def record_candidate(
    cls,
    block_id: BlockID,
  ) -> CandidateWriteResult:
    return await record_candidate(cls, block_id)

  @classmethod
  async def record_supersession(
    cls,
    successor_block_id: BlockID,
    predecessor_block_id: BlockID,
  ) -> RelationWriteResult:
    async with graph_uow() as uow:
      await require_distinct_blocks(successor_block_id, predecessor_block_id, uow)
      if await cls._has_directed_path(
        predecessor_block_id,
        successor_block_id,
        uow=uow,
      ):
        raise ValueError(
          f"Cannot supersede: an existing supersedes path runs from predecessor "
          f"{predecessor_block_id} to successor {successor_block_id}"
        )
      relation, created = await fetchsert_relation(
        successor_block_id,
        predecessor_block_id,
        SUPERSEDES_RELATION,
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
          frontier, content=SUPERSEDES_RELATION
        )
      )
      next_frontier = set(rows) - visited
      if target in next_frontier:
        return True
      visited.update(next_frontier)
      frontier = next_frontier
    return False

  async def read_lineage(
    self,
    focal_block_id: BlockID,
    *,
    max_explored_blocks: int = 1000,
    max_explored_relations: int = 10000,
  ) -> SupersessionLineage:
    """Read supersedes history; incomplete or cyclic graphs have no current frontier."""
    if max_explored_blocks < 1 or max_explored_relations < 1:
      raise ValueError("exploration bounds must be positive")
    return await self._read_lineage(
      focal_block_id,
      max_explored_blocks=max_explored_blocks,
      max_explored_relations=max_explored_relations,
    )

  async def _read_lineage(
    self,
    focal_block_id: BlockID,
    *,
    max_explored_blocks: int,
    max_explored_relations: int,
  ) -> SupersessionLineage:
    async with graph_uow() as uow:
      if (await uow.blocks.get(focal_block_id)) is None:
        raise ValueError("Focal Block does not exist")
      visited = {focal_block_id}
      frontier = deque((focal_block_id,))
      relations: dict[RelationID, RelationModel] = {}
      scanned = 0
      truncated = False
      while frontier and not truncated:
        current = frontier.popleft()
        for endpoint in ("from", "to"):
          cursor: RelationID | None = None
          while not truncated:
            remaining = max_explored_relations - scanned
            if remaining == 0:
              truncated = True
              break
            requested = min(200, remaining + 1)
            page = await uow.relations.get_endpoint_page(
              (current,),
              endpoint=typing.cast(typing.Literal["from", "to"], endpoint),
              contents=(SUPERSEDES_RELATION,),
              cursor=cursor,
              limit=requested,
            )
            if len(page) > remaining:
              page = page[:remaining]
              truncated = True
            scanned += len(page)
            for relation in page:
              if relation.id is None:
                continue
              relations[relation.id] = relation
              neighbor = relation.to_ if relation.from_ == current else relation.from_
              if neighbor in visited:
                continue
              if len(visited) >= max_explored_blocks:
                truncated = True
                break
              visited.add(neighbor)
              frontier.append(neighbor)
            if truncated or len(page) < requested:
              break
            cursor = typing.cast(RelationID, page[-1].id)

      cycle_detected = self._cycle_detected(visited, tuple(relations.values()))
      incoming = {relation.to_ for relation in relations.values()}
      current_block_ids = (
        ()
        if truncated or cycle_detected
        else tuple(sorted(block_id for block_id in visited if block_id not in incoming))
      )
      blocks = await uow.blocks.get_many(visited)
      existing = {block.id for block in blocks}
      closed_relations = tuple(
        relation
        for relation in relations.values()
        if relation.from_ in existing and relation.to_ in existing
      )
    return SupersessionLineage(
      graph=GraphModel(blocks=blocks, relations=closed_relations),
      current_block_ids=current_block_ids,
      truncated=truncated,
      cycle_detected=cycle_detected,
    )

  @staticmethod
  def _cycle_detected(
    blocks: typing.Collection[BlockID],
    relations: typing.Collection[RelationModel],
  ) -> bool:
    # The standard library uses an explicit stack, so long lineages do not
    # consume Python call-stack depth.
    sorter: TopologicalSorter[BlockID] = TopologicalSorter()
    for block_id in blocks:
      sorter.add(block_id)
    for relation in relations:
      sorter.add(relation.to_, relation.from_)
    try:
      sorter.prepare()
    except CycleError:
      return True
    return False

  @classmethod
  async def can_run_automatic(cls) -> bool:
    return await configured_agent_available(SUPERSESSION_CONFIG_KEY, BehaviorAgentConfig)

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
          "Determine only well-supported scoped supersession relations.",
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
          SUPERSESSION_CONFIG_KEY,
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
