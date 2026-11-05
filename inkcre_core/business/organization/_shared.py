"""Small shared mechanics for exact Organization behaviors."""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import typing

import pydantic

from inkcre_core.business.agent import AgentManager, AgentNotFoundError, TurnTermination
from inkcre_core.business.deployment_config import DeploymentConfigService
from inkcre_core.business.info_base.resolver import (
  Resolver,
  ResolverManager,
  UnknownResolverError,
  UnsupportedResolverCapability,
)
from inkcre_core.persistence.info_base.uow import GraphUnitOfWork, graph_uow
from inkcre_core.schemas.ai import TextContentPart, UserMessage
from inkcre_core.schemas.info_base.block import BlockForm, BlockID, BlockModel, ResolverType
from inkcre_core.schemas.info_base.relation import RelationID, RelationModel
from inkcre_core.schemas.organization_behavior import (
  CandidateWriteResult,
  RelationWriteResult,
)

from .contracts import (
  OrganizationAgentNotFoundError,
  OrganizationBlockNotFoundError,
  OrganizationBudgetExceededError,
  OrganizationExecutionError,
  OrganizationNotConfiguredError,
)


CANDIDATE_RELATION = "candidate for"
_CONTEXT_RELATION_LIMIT = 20


def behavior_resolver_classes() -> tuple[type[Resolver], ...]:
  classes: list[type[Resolver]] = []
  for resolver_cls in ResolverManager.RESOLVER_CLS.values():
    if (
      issubclass(resolver_cls, Resolver)
      and isinstance(getattr(resolver_cls, "organization_description", None), str)
      and callable(getattr(resolver_cls, "record_candidate", None))
    ):
      classes.append(resolver_cls)
  return tuple(sorted(classes, key=lambda resolver_cls: resolver_cls.__rsotype__))


def get_behavior_resolver(resolver: ResolverType) -> type[Resolver] | None:
  return next(
    (
      resolver_cls
      for resolver_cls in behavior_resolver_classes()
      if resolver_cls.__rsotype__ == resolver
    ),
    None,
  )


async def get_or_create_descriptor(
  behavior: type[Resolver],
  uow: GraphUnitOfWork,
) -> BlockModel:
  form = BlockForm(resolver=behavior.__rsotype__, content="")
  existing = await ResolverManager.get(BlockModel.model_validate(form)).get_existing_async(
    uow.blocks
  )
  return existing if existing is not None else await uow.blocks.create(form)


async def record_candidate(
  behavior: type[Resolver],
  block_id: BlockID,
) -> CandidateWriteResult:
  async with graph_uow() as uow:
    if (await uow.blocks.get(block_id)) is None:
      raise OrganizationBlockNotFoundError(f"Block {block_id} does not exist")
    descriptor = await get_or_create_descriptor(behavior, uow)
    descriptor_id = _block_id(descriptor)
    if block_id == descriptor_id:
      raise ValueError("An Organization behavior cannot be its own candidate")
    relation, created = await fetchsert_relation(
      block_id,
      descriptor_id,
      CANDIDATE_RELATION,
      uow,
    )
    return CandidateWriteResult(
      descriptor_block_id=descriptor_id,
      relation_id=_relation_id(relation),
      created=created,
    )


async def candidate_seed_ids(
  behavior: type[Resolver],
  limit: int,
) -> tuple[BlockID, ...]:
  if limit <= 0:
    return ()
  async with graph_uow() as uow:
    descriptor = await get_or_create_descriptor(behavior, uow)
    descriptor_id = _block_id(descriptor)
    return await uow.relations.random_incoming_sources(
      descriptor_id, CANDIDATE_RELATION, limit
    )


async def recent_block_ids(limit: int) -> tuple[BlockID, ...]:
  if limit <= 0:
    return ()
  behavior_types = tuple(
    resolver_cls.__rsotype__ for resolver_cls in behavior_resolver_classes()
  )
  async with graph_uow() as uow:
    return await uow.blocks.select_ids(
      limit, exclude_resolvers=behavior_types, random_order=False
    )


async def random_block_ids(limit: int) -> tuple[BlockID, ...]:
  if limit <= 0:
    return ()
  behavior_types = tuple(
    resolver_cls.__rsotype__ for resolver_cls in behavior_resolver_classes()
  )
  async with graph_uow() as uow:
    return await uow.blocks.select_ids(
      limit, exclude_resolvers=behavior_types, random_order=True
    )


async def recent_relation_endpoint_ids(
  limit: int,
  *,
  contents: typing.Collection[str] = (),
) -> tuple[BlockID, ...]:
  if limit <= 0:
    return ()
  async with graph_uow() as uow:
    relations = await uow.relations.recent(limit, contents=contents)
  return tuple(
    dict.fromkeys(
      endpoint for relation in relations for endpoint in (relation.from_, relation.to_)
    )
  )


def merge_seed_categories(
  max_seeds: int,
  *categories: typing.Collection[BlockID],
) -> tuple[BlockID, ...]:
  """Reserve one position per non-empty category, then fill in priority order."""
  result: list[BlockID] = []
  normalized = [tuple(dict.fromkeys(category)) for category in categories]
  for category in normalized:
    if category and category[0] not in result:
      result.append(category[0])
      if len(result) == max_seeds:
        return tuple(result)
  for category in normalized:
    for block_id in category[1:]:
      if block_id not in result:
        result.append(block_id)
        if len(result) == max_seeds:
          return tuple(result)
  return tuple(result)


async def configured_agent_available(
  config_key: str,
  config_type: type[pydantic.BaseModel],
) -> bool:
  config = await DeploymentConfigService.get(config_key)
  if config is None:
    return False
  if not isinstance(config, config_type):
    raise TypeError(f"Organization config {config_key!r} returned the wrong model")
  return await AgentManager.can_execute(typing.cast(typing.Any, config).agent, "text")


@contextmanager
def continue_after_seed_failure(
  logger: logging.Logger,
  behavior: ResolverType,
  seed_block_id: BlockID,
) -> typing.Generator[None, None, None]:
  """Recover only candidate-local failures in an automatic attempt."""
  try:
    yield
  except (OrganizationBlockNotFoundError, OrganizationBudgetExceededError) as error:
    logger.warning(
      "organization.seed.considered",
      extra={
        "behavior": behavior,
        "seed_block_ids": (seed_block_id,),
        "outcome": "recoverable_failure",
        "reason": (
          "seed_missing"
          if isinstance(error, OrganizationBlockNotFoundError)
          else "model_call_limit"
        ),
      },
    )


async def run_configured_agent(
  config_key: str,
  config_type: type[pydantic.BaseModel],
  message: UserMessage,
) -> None:
  config = await DeploymentConfigService.get(config_key)
  if config is None:
    raise OrganizationNotConfiguredError(
      f"Organization behavior {config_key!r} is not configured"
    )
  if not isinstance(config, config_type):
    raise TypeError(f"Organization config {config_key!r} returned the wrong model")
  agent_id = typing.cast(typing.Any, config).agent
  try:
    thread = await AgentManager.run(agent_id, message)
  except AgentNotFoundError as error:
    raise OrganizationAgentNotFoundError(
      f"Configured Organization Agent {agent_id} does not exist"
    ) from error
  turn = thread.current_turn
  if turn is None:  # pragma: no cover - AgentManager.run invariant
    raise OrganizationExecutionError("Organization Agent did not start a Turn")
  outcome = await turn
  if outcome == TurnTermination.MAX_MODEL_CALLS:
    raise OrganizationBudgetExceededError(
      "Organization Agent exceeded its per-Turn model-call budget"
    )


async def build_seed_message(
  request: str,
  judgment_contract: tuple[str, ...],
  seed_id: BlockID,
) -> UserMessage | None:
  """Resolve one bounded seed neighborhood without holding a DB transaction."""
  async with graph_uow() as uow:
    block = await uow.blocks.get(seed_id)
    if block is None:
      raise OrganizationBlockNotFoundError(f"Block {seed_id} does not exist")
    relations = tuple(
      sorted(
        (await uow.relations.get(seed_id)),
        key=lambda relation: relation.id or 0,
      )[-_CONTEXT_RELATION_LIMIT:]
    )
    neighbor_ids = {
      relation.to_ if relation.from_ == seed_id else relation.from_
      for relation in relations
    }
    neighbors = {
      neighbor.id: neighbor
      for neighbor in (await uow.blocks.get_many(neighbor_ids))
      if neighbor.id is not None
    }

  try:
    resolver = ResolverManager.get(block)
    text = await resolver.get_text(materialize_missing=False)
    label = await resolver.get_label()
  except (UnknownResolverError, UnsupportedResolverCapability):
    return None
  if text is None or not text.strip():
    return None

  relation_context: list[dict[str, typing.Any]] = []
  for relation in relations:
    other_id = relation.to_ if relation.from_ == seed_id else relation.from_
    other = neighbors.get(other_id)
    other_label = None
    if other is not None:
      try:
        other_label = await ResolverManager.get(other).get_label()
      except (UnknownResolverError, UnsupportedResolverCapability):
        pass
    relation_context.append(
      {
        "id": relation.id,
        "direction": "outgoing" if relation.from_ == seed_id else "incoming",
        "content": relation.content,
        "other_block": {
          "id": other_id,
          "resolver": other.resolver if other is not None else None,
          "label": other_label,
        },
      }
    )
  context = {
    "request": request,
    "judgment_contract": judgment_contract,
    "seed_block": {
      "id": seed_id,
      "resolver": block.resolver,
      "label": label,
      "text": text,
    },
    "direct_relations": relation_context,
    "exploration": (
      "The seed and its direct relations are only a starting point. Use the declared "
      "retrieval, Resolver, and graph tools when more evidence is needed. Persist only "
      "through the exact behavior tool, or cautiously mark a different behavior candidate."
    ),
  }
  return UserMessage(
    content=(
      TextContentPart(
        text=json.dumps(
          context,
          ensure_ascii=False,
          sort_keys=True,
          separators=(",", ":"),
        )
      ),
    )
  )


async def fetchsert_relation(
  from_: BlockID,
  to_: BlockID,
  content: str,
  uow: GraphUnitOfWork,
) -> tuple[RelationModel, bool]:
  proposed = RelationModel(from_=from_, to_=to_, content=content)
  relation = await uow.relations.fetchsert(proposed)
  return relation, relation is proposed


def relation_result(relation: RelationModel, created: bool) -> RelationWriteResult:
  return RelationWriteResult(relation_id=_relation_id(relation), created=created)


async def require_distinct_blocks(
  left: BlockID,
  right: BlockID,
  uow: GraphUnitOfWork,
) -> None:
  if left == right:
    raise ValueError("Organization relation endpoints must be different")
  found = {block.id for block in (await uow.blocks.get_many((left, right)))}
  missing = tuple(block_id for block_id in (left, right) if block_id not in found)
  if missing:
    raise OrganizationBlockNotFoundError(f"Blocks do not exist: {missing!r}")


def _block_id(block: BlockModel) -> BlockID:
  if block.id is None:  # pragma: no cover - persisted Block invariant
    raise RuntimeError("Persisted Block has no ID")
  return block.id


def _relation_id(relation: RelationModel) -> RelationID:
  if relation.id is None:  # pragma: no cover - persisted Relation invariant
    raise RuntimeError("Persisted Relation has no ID")
  return relation.id
