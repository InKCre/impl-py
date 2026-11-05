"""Explicit and automatic rumination carried by an exact Behavior Resolver."""

from __future__ import annotations

import json
import typing

import pydantic

from inkcre_core.business.deployment_config import DeploymentConfigManager
from inkcre_core.business.info_base.resolver import (
  Resolver,
  ResolverManager,
  UnknownResolverError,
  UnsupportedResolverCapability,
)
from inkcre_core.business.peer import PeerManager
from inkcre_core.persistence.info_base.uow import graph_uow
from inkcre_core.libs.obsrv.main import get_logger
from inkcre_core.schemas.ai import JSONValue, TextContentPart, UserMessage
from inkcre_core.schemas.info_base.block import BlockID
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.organization import RuminationConfig, RuminationRequest
from inkcre_core.schemas.organization_behavior import CandidateWriteResult
from inkcre_core.schemas.peer import PeerProtocolRequest, PeerProtocolResponse, PeerRef

from ._shared import (
  candidate_seed_ids,
  configured_agent_available,
  continue_after_seed_failure,
  merge_seed_categories,
  random_block_ids,
  recent_block_ids,
  record_candidate,
  run_configured_agent,
)
from .contracts import (
  OrganizationBlockNotFoundError,
  OrganizationDelegationError,
  OrganizationExecutionError,
)


LOGGER = get_logger().getChild(__name__)

RUMINATION_CONFIG_KEY = "core.organization.rumination"
RUMINATION_CONFIG_SCHEMA = "core.organization.rumination.config.v1"
RUMINATION_CAPABILITY = "core.organization.rumination.v1"
RUMINATION_BEHAVIOR = "core.organization.behavior.rumination.v1"

DeploymentConfigManager.register_schema(
  RUMINATION_CONFIG_SCHEMA, RuminationConfig, keys=(RUMINATION_CONFIG_KEY,)
)


class RuminationBehaviorResolver(
  Resolver[str, str],
  rso_type=RUMINATION_BEHAVIOR,
):
  organization_description = (
    "Open-ended reconsideration of one information Block that may add a useful graph."
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
    return "organization behavior: rumination"

  @classmethod
  async def record_candidate(
    cls,
    block_id: BlockID,
  ) -> CandidateWriteResult:
    return await record_candidate(cls, block_id)

  @classmethod
  async def can_run_automatic(cls) -> bool:
    return await configured_agent_available(RUMINATION_CONFIG_KEY, RuminationConfig)

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
        await cls.ruminate_local(seed)
        LOGGER.info(
          "organization.seed.considered",
          extra={
            "behavior": cls.__rsotype__,
            "seed_block_ids": (seed,),
            "outcome": "considered",
            "reason": "agent_completed",
          },
        )

  @classmethod
  async def ruminate(
    cls,
    block_id: BlockID,
    *,
    route_to_peer: PeerRef | None = None,
  ) -> None:
    """Execute locally unless the caller explicitly selects another Peer."""
    request = RuminationRequest(block=block_id)
    if route_to_peer is None or route_to_peer == PeerManager.get_current_peer_ref():
      await cls.ruminate_local(request.block)
      return

    payload = PeerProtocolRequest(
      body=typing.cast(JSONValue, request.model_dump(mode="json"))
    )
    result = await PeerManager.delegate(
      RUMINATION_CAPABILITY,
      typing.cast(JSONValue, payload.model_dump(mode="json", exclude_unset=True)),
      route_to_peer=route_to_peer,
    )
    try:
      response = PeerProtocolResponse.model_validate(result)
    except pydantic.ValidationError as error:
      raise OrganizationDelegationError(
        "Rumination Peer returned an invalid response"
      ) from error
    if response.status != 204 or "body" in response.model_fields_set:
      raise OrganizationDelegationError(f"Rumination Peer returned HTTP {response.status}")

  @classmethod
  async def ruminate_local(cls, block_id: BlockID) -> None:
    initial_message = await cls._build_initial_message(block_id)
    if initial_message is None:
      return
    await run_configured_agent(
      RUMINATION_CONFIG_KEY,
      RuminationConfig,
      initial_message,
    )

  @classmethod
  async def _build_initial_message(cls, block_id: BlockID) -> UserMessage | None:
    async with graph_uow() as uow:
      block = await uow.blocks.get(block_id)
      if block is None:
        raise OrganizationBlockNotFoundError(f"Block {block_id} does not exist")
      relations = tuple(
        sorted(
          (await uow.relations.get(block_id)),
          key=lambda relation: relation.id or 0,
        )
      )
      neighbor_ids = {
        relation.to_ if relation.from_ == block_id else relation.from_
        for relation in relations
      }
      neighbors = {
        neighbor.id: neighbor for neighbor in await uow.blocks.get_many(neighbor_ids)
      }

    try:
      focal_text = await ResolverManager.get(block).get_text()
    except (UnknownResolverError, UnsupportedResolverCapability):
      return None
    except Exception as error:
      raise OrganizationExecutionError(
        "Rumination could not understand focal Block"
      ) from error
    if focal_text is None or not focal_text.strip():
      return None

    relation_context: list[dict[str, typing.Any]] = []
    for relation in relations:
      neighbor_id, direction = cls._neighbor_and_direction(block_id, relation)
      neighbor = neighbors.get(neighbor_id)
      label: str | None = None
      if neighbor is not None:
        try:
          label = await ResolverManager.get(neighbor).get_label()
        except Exception:
          LOGGER.debug(
            "Could not project rumination neighbor label",
            exc_info=True,
            extra={"block": neighbor_id},
          )
      relation_context.append(
        {
          "id": relation.id,
          "direction": direction,
          "property": relation.content,
          "other_block": {
            "id": neighbor_id,
            "resolver": neighbor.resolver if neighbor is not None else None,
            "label": label,
          },
        }
      )

    context = {
      "request": "ruminate",
      "focal_block": {
        "id": block_id,
        "resolver": block.resolver,
        "text": focal_text,
      },
      "direct_relations": relation_context,
      "available_draft_resolvers": [
        {
          "resolver": capability.resolver,
          "description": capability.description,
        }
        for capability in ResolverManager.get_draft_capabilities()
      ],
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

  @staticmethod
  def _neighbor_and_direction(
    block_id: BlockID,
    relation: RelationModel,
  ) -> tuple[BlockID, typing.Literal["incoming", "outgoing", "self"]]:
    if relation.from_ == block_id and relation.to_ == block_id:
      return block_id, "self"
    if relation.from_ == block_id:
      return relation.to_, "outgoing"
    return relation.from_, "incoming"
