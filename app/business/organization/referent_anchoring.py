"""Existing-referent anchoring through occurrence-local selected text."""

from __future__ import annotations

import typing


from app.business.deployment_config import DeploymentConfigManager
from app.business.info_base.resolver import Resolver, ResolverManager
from app.persistence.info_base.uow import GraphUnitOfWork, graph_uow
from libs.obsrv.main import get_logger
from app.schemas.info_base.block import BlockForm, BlockID
from app.schemas.info_base.relation import RelationModel
from app.schemas.organization_behavior import (
  BehaviorAgentConfig,
  CandidateWriteResult,
  ExistingReferentAnchorProposal,
  ExistingReferentAnchorResult,
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

HAS_MENTION_RELATION = "has mention"
REFERS_TO_RELATION = "refers to"
REFERENT_ANCHORING_BEHAVIOR = "core.organization.behavior.existing-referent-anchoring.v1"
REFERENT_ANCHORING_CONFIG_KEY = "core.organization.existing_referent_anchoring"
REFERENT_ANCHORING_CONFIG_SCHEMA = "core.organization.existing_referent_anchoring.config.v1"

DeploymentConfigManager.register_schema(
  REFERENT_ANCHORING_CONFIG_SCHEMA,
  BehaviorAgentConfig,
  keys=(REFERENT_ANCHORING_CONFIG_KEY,),
)


class ExistingReferentAnchoringBehaviorResolver(
  Resolver[str, str],
  rso_type=REFERENT_ANCHORING_BEHAVIOR,
):
  organization_description = (
    "Anchor one source-grounded referring fragment to existing "
    "identity-bearing information."
  )
  judgment_contract = (
    "The source expression meaningfully denotes a reusable referent.",
    "Selected text identifies this mention without unrelated material.",
    "The referent Block already exists and was not created as a temporary label.",
    "The target contains enough identity to distinguish plausible alternatives.",
    "Denotation remains continuous across name, time, environment, and scope.",
    "Plausible competing referents have been considered and excluded.",
    "The anchor improves cross-source or cross-time use rather than graph density.",
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
    return "organization behavior: existing referent anchoring"

  @classmethod
  async def record_candidate(
    cls,
    block_id: BlockID,
  ) -> CandidateWriteResult:
    return await record_candidate(cls, block_id)

  @classmethod
  async def anchor_existing_referent(
    cls,
    source_block_id: BlockID,
    selected_text: str,
    referent_block_id: BlockID,
  ) -> ExistingReferentAnchorResult:
    proposal = ExistingReferentAnchorProposal(
      source_block_id=source_block_id,
      selected_text=selected_text,
      referent_block_id=referent_block_id,
    )
    async with graph_uow() as uow:
      source = await uow.blocks.get(proposal.source_block_id)
      if source is None:
        raise ValueError("Source Block does not exist")
    # Resolve outside the write transaction; content may be an opaque Storage pointer.
    source_text = await ResolverManager.get(source).get_text(materialize_missing=False)
    if source_text is None or proposal.selected_text not in source_text:
      raise ValueError("Selected text does not occur in the source text")
    async with graph_uow() as uow:
      await require_distinct_blocks(
        proposal.source_block_id, proposal.referent_block_id, uow
      )
      source = await uow.blocks.get(proposal.source_block_id)
      if source is None:  # pragma: no cover - require_distinct_blocks invariant
        raise ValueError("Source Block does not exist")

      existing = await cls._existing_path(
        proposal.source_block_id,
        proposal.selected_text,
        proposal.referent_block_id,
        uow,
      )
      if existing is not None:
        fragment_id, has_mention, refers_to = existing
        return ExistingReferentAnchorResult(
          fragment_block_id=fragment_id,
          fragment_created=False,
          has_mention=(
            relation_result(has_mention, False) if has_mention is not None else None
          ),
          refers_to=relation_result(refers_to, False),
        )

      source_is_fragment = (
        source.resolver == "core.text.v1"
        and source.storage is None
        and source.content == proposal.selected_text
      )
      if source_is_fragment:
        fragment_id = proposal.source_block_id
        fragment_created = False
        has_mention_result = None
      else:
        fragment = await uow.blocks.create(
          BlockForm(resolver="core.text.v1", content=proposal.selected_text)
        )
        if fragment.id is None:  # pragma: no cover - persisted Block invariant
          raise RuntimeError("Persisted referring fragment has no ID")
        fragment_id = fragment.id
        fragment_created = True
        has_mention, created = await fetchsert_relation(
          proposal.source_block_id,
          fragment_id,
          HAS_MENTION_RELATION,
          uow,
        )
        has_mention_result = relation_result(has_mention, created)

      refers_to, created = await fetchsert_relation(
        fragment_id,
        proposal.referent_block_id,
        REFERS_TO_RELATION,
        uow,
      )
      return ExistingReferentAnchorResult(
        fragment_block_id=fragment_id,
        fragment_created=fragment_created,
        has_mention=has_mention_result,
        refers_to=relation_result(refers_to, created),
      )

  @staticmethod
  async def _existing_path(
    source_block_id: BlockID,
    selected_text: str,
    referent_block_id: BlockID,
    uow: GraphUnitOfWork,
  ) -> tuple[BlockID, RelationModel | None, RelationModel] | None:
    source = await uow.blocks.get(source_block_id)
    if (
      source is not None
      and source.resolver == "core.text.v1"
      and source.storage is None
      and source.content == selected_text
    ):
      refers_to = next(
        (
          relation
          for relation in (
            await uow.relations.get(
              source_block_id, include_in=False, content=REFERS_TO_RELATION
            )
          )
          if relation.to_ == referent_block_id
        ),
        None,
      )
      if refers_to is not None:
        return source_block_id, None, refers_to

    for has_mention in await uow.relations.get(
      source_block_id, include_in=False, content=HAS_MENTION_RELATION
    ):
      fragment = await uow.blocks.get(has_mention.to_)
      if (
        fragment is None
        or fragment.resolver != "core.text.v1"
        or fragment.storage is not None
        or fragment.content != selected_text
      ):
        continue
      refers_to = next(
        (
          relation
          for relation in (
            await uow.relations.get(
              has_mention.to_, include_in=False, content=REFERS_TO_RELATION
            )
          )
          if relation.to_ == referent_block_id
        ),
        None,
      )
      if refers_to is not None:
        return has_mention.to_, has_mention, refers_to
    return None

  @classmethod
  async def can_run_automatic(cls) -> bool:
    return await configured_agent_available(
      REFERENT_ANCHORING_CONFIG_KEY,
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
          "Anchor only resolved source mentions to existing identity-bearing Blocks.",
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
          REFERENT_ANCHORING_CONFIG_KEY,
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
