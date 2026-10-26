"""Provenance-preserving n-ary synthesis behavior."""

from __future__ import annotations

import typing


from app.business.deployment_config import DeploymentConfigManager
from app.business.info_base.resolver import Resolver
from app.persistence.info_base.uow import GraphUnitOfWork, graph_uow
from libs.obsrv.main import get_logger
from app.schemas.info_base.block import BlockForm, BlockID, BlockModel
from app.schemas.organization_behavior import (
  BehaviorAgentConfig,
  CandidateWriteResult,
  SynthesisProposal,
  SynthesisWriteResult,
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
  run_configured_agent,
)
from .supersession import EDITED_RELATION


LOGGER = get_logger().getChild(__name__)

SYNTHESIS_RELATION = "synthesis"
SYNTHESIS_BEHAVIOR = "core.organization.behavior.synthesis.v1"
SYNTHESIS_CONFIG_KEY = "core.organization.synthesis"
SYNTHESIS_CONFIG_SCHEMA = "core.organization.synthesis.config.v1"

DeploymentConfigManager.register_schema(
  SYNTHESIS_CONFIG_SCHEMA, BehaviorAgentConfig, keys=(SYNTHESIS_CONFIG_KEY,)
)


class SynthesisBehaviorResolver(
  Resolver[str, str],
  rso_type=SYNTHESIS_BEHAVIOR,
):
  organization_description = (
    "Create reusable multi-source information while preserving exact source basis."
  )
  judgment_contract = (
    "The result is independently useful information, not a list of related sources.",
    "Every source materially contributes content, attribution, or uncertainty.",
    "Source disagreements, uncertainty, and speaker attribution remain visible.",
    "Scopes are compatible or their differences are explicitly preserved.",
    "Duplicate-connected copies do not multiply independent corroboration.",
    "No existing synthesis already provides the same reusable distinction.",
    "Observed past use and recurrence make future reuse plausible.",
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
    return "organization behavior: synthesis"

  @classmethod
  async def record_candidate(
    cls,
    block_id: BlockID,
  ) -> CandidateWriteResult:
    return await record_candidate(cls, block_id)

  @classmethod
  async def create_synthesis(
    cls,
    text: str,
    source_block_ids: typing.Collection[BlockID],
    previous_synthesis_block_id: BlockID | None = None,
  ) -> SynthesisWriteResult:
    proposal = SynthesisProposal(
      text=text,
      source_block_ids=tuple(source_block_ids),
      previous_synthesis_block_id=previous_synthesis_block_id,
    )
    async with graph_uow() as uow:
      found = {block.id for block in (await uow.blocks.get_many(proposal.source_block_ids))}
      missing = tuple(source for source in proposal.source_block_ids if source not in found)
      if missing:
        raise ValueError(f"Synthesis source Blocks do not exist: {missing!r}")
      if proposal.previous_synthesis_block_id is not None:
        previous = await uow.blocks.get(proposal.previous_synthesis_block_id)
        if previous is None:
          raise ValueError("Previous synthesis Block does not exist")
        previous_basis = await cls._source_basis(previous, uow)
        if len(set(previous_basis)) < 2:
          raise ValueError("Previous synthesis has no valid multi-source basis")

      source_set = set(proposal.source_block_ids)
      text_candidates = await uow.blocks.matching_content("core.text.v1", proposal.text)
      synthesis = None
      for candidate in text_candidates:
        if (
          candidate.storage is None
          and await cls._source_basis(candidate, uow) == source_set
        ):
          synthesis = candidate
          break
      synthesis_created = synthesis is None
      if synthesis is None:
        synthesis = await uow.blocks.create(
          BlockForm(resolver="core.text.v1", content=proposal.text)
        )
      if synthesis.id is None:  # pragma: no cover - persisted Block invariant
        raise RuntimeError("Persisted synthesis Block has no ID")

      basis = []
      for source_block_id in sorted(source_set):
        relation, created = await fetchsert_relation(
          source_block_id,
          synthesis.id,
          SYNTHESIS_RELATION,
          uow,
        )
        basis.append(relation_result(relation, created))

      edited = None
      if (
        proposal.previous_synthesis_block_id is not None
        and proposal.previous_synthesis_block_id != synthesis.id
      ):
        relation, created = await fetchsert_relation(
          proposal.previous_synthesis_block_id,
          synthesis.id,
          EDITED_RELATION,
          uow,
        )
        edited = relation_result(relation, created)
      return SynthesisWriteResult(
        synthesis_block_id=synthesis.id,
        synthesis_created=synthesis_created,
        basis=tuple(basis),
        edited=edited,
      )

  @staticmethod
  async def _source_basis(
    synthesis: BlockModel,
    uow: GraphUnitOfWork,
  ) -> set[BlockID]:
    if synthesis.id is None:
      return set()
    return {
      relation.from_
      for relation in await uow.relations.get(
        synthesis.id, include_out=False, content=SYNTHESIS_RELATION
      )
    }

  @classmethod
  async def _change_signal_ids(cls, limit: int) -> tuple[BlockID, ...]:
    endpoints = await recent_relation_endpoint_ids(limit)
    if not endpoints:
      return ()
    async with graph_uow() as uow:
      affected = tuple(
        relation.to_
        for relation in await uow.relations.get_outgoing_many(
          endpoints, content=SYNTHESIS_RELATION
        )
      )
    return tuple(dict.fromkeys((*endpoints, *affected)))

  @classmethod
  async def can_run_automatic(cls) -> bool:
    return await configured_agent_available(SYNTHESIS_CONFIG_KEY, BehaviorAgentConfig)

  @classmethod
  async def run_automatic(cls, max_seeds: int) -> None:
    candidates = await candidate_seed_ids(cls, max_seeds)
    strong = merge_seed_categories(
      max_seeds,
      await cls._change_signal_ids(max_seeds),
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
          "Create only provenance-preserving, reusable multi-source synthesis.",
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
        await run_configured_agent(SYNTHESIS_CONFIG_KEY, BehaviorAgentConfig, message)
        LOGGER.info(
          "organization.seed.considered",
          extra={
            "behavior": cls.__rsotype__,
            "seed_block_ids": (seed,),
            "outcome": "considered",
            "reason": "agent_completed",
          },
        )
