"""System-driven media interpretation approach owned by Organization."""

from app.contracts.jobs import (
  MEDIA_INTERPRETATION_JOB_TYPE as MEDIA_INTERPRETATION_JOB_TYPE,
)

import json
import logging
import typing


from app.business.agent import AgentManager, TurnTermination
from app.business.deployment_config import DeploymentConfigManager, DeploymentConfigService
from app.business.info_base.commands import get_related_block
from app.business.info_base.resolver import ResolverManager
from app.business.info_base.resolver.audio import AudioSolvedContent
from app.business.info_base.resolver.image import ImageSolvedContent
from app.business.info_base.resolver.video import VideoSolvedContent
from app.persistence.info_base.uow import graph_uow
from app.schemas.ai import (
  AudioContentPart,
  ImageContentPart,
  TextContentPart,
  UserContentPart,
  UserMessage,
  VideoContentPart,
)
from app.schemas.info_base.block import BlockModel
from app.schemas.organization import (
  MediaInterpretationConfig,
  MediaInterpretationDiagnostic,
  MediaInterpretationReport,
)


logger = logging.getLogger(__name__)

MEDIA_INTERPRETATION_CONFIG_KEY = "core.organization.media_interpretation"
MEDIA_INTERPRETATION_CONFIG_SCHEMA = "core.organization.media_interpretation.config.v1"
_CANDIDATE_LIMIT = 20
_DIAGNOSTIC_LIMIT = 20
_RESOLVER_MODALITIES: dict[str, typing.Literal["image", "audio", "video"]] = {
  "core.image.v1": "image",
  "core.audio.v1": "audio",
  "core.video.v1": "video",
}


DeploymentConfigManager.register_schema(
  MEDIA_INTERPRETATION_CONFIG_SCHEMA,
  MediaInterpretationConfig,
  keys=(MEDIA_INTERPRETATION_CONFIG_KEY,),
)


async def _config() -> MediaInterpretationConfig | None:
  value = await DeploymentConfigService.get(MEDIA_INTERPRETATION_CONFIG_KEY)
  if value is None:
    return None
  if not isinstance(value, MediaInterpretationConfig):
    raise TypeError("Media interpretation config registry returned the wrong model")
  return value


def _agent(
  config: MediaInterpretationConfig,
  modality: typing.Literal["image", "audio", "video"],
) -> int:
  return {
    "image": config.image_agent,
    "audio": config.audio_agent,
    "video": config.video_agent,
  }[modality]


async def can_handle_media_interpretation() -> bool:
  config = await _config()
  if config is None:
    return False
  for modality in typing.cast(
    tuple[typing.Literal["image", "audio", "video"], ...],
    ("image", "audio", "video"),
  ):
    if await AgentManager.can_execute(_agent(config, modality), modality):
      return True
  return False


async def _candidates() -> tuple[BlockModel, ...]:
  async with graph_uow() as uow:
    return await uow.blocks.without_outgoing_relation(
      tuple(_RESOLVER_MODALITIES), "interpretation", limit=_CANDIDATE_LIMIT
    )


async def _relation_context(block_id: int) -> list[dict[str, typing.Any]]:
  async with graph_uow() as uow:
    relations = (await uow.relations.get(block_id))[:20]
    neighbor_ids = {
      relation.to_ if relation.from_ == block_id else relation.from_
      for relation in relations
    }
    neighbors = {block.id: block for block in await uow.blocks.get_many(neighbor_ids)}
  context: list[dict[str, typing.Any]] = []
  for relation in relations:
    other_id = relation.to_ if relation.from_ == block_id else relation.from_
    other = neighbors.get(other_id)
    label = None
    if other is not None:
      try:
        label = await ResolverManager.get(other).get_label()
      except Exception:
        logger.debug("Could not project media neighbor label", exc_info=True)
    context.append(
      {
        "direction": "outgoing" if relation.from_ == block_id else "incoming",
        "property": relation.content,
        "other_block": {"id": other_id, "label": label},
      }
    )
  return context


async def _message(block: BlockModel) -> UserMessage | None:
  resolver = ResolverManager.get(block)
  solved = await resolver.get_solved_content(materialize_missing=False)
  transfer_url = await resolver.get_transfer_url()
  media: UserContentPart
  facts: dict[str, typing.Any]
  if isinstance(solved, ImageSolvedContent):
    media_type = solved.detected_media_type or (
      f"image/{solved.format}" if solved.format is not None else None
    )
    if media_type is None:
      return None
    media = ImageContentPart(
      data=solved.content,
      mime_type=media_type,
      transfer_url=transfer_url,
    )
    facts = {"format": solved.format, "width": solved.width, "height": solved.height}
  elif isinstance(solved, AudioSolvedContent):
    media_type = solved.detected_media_type or (
      f"audio/{solved.container}" if solved.container is not None else None
    )
    if media_type is None:
      return None
    media = AudioContentPart(
      data=solved.content,
      mime_type=media_type,
      transfer_url=transfer_url,
    )
    facts = {
      "container": solved.container,
      "codec": solved.codec,
      "duration_ms": solved.duration_ms,
    }
  elif isinstance(solved, VideoSolvedContent):
    media_type = solved.detected_media_type or (
      f"video/{solved.container}" if solved.container is not None else None
    )
    if media_type is None:
      return None
    media = VideoContentPart(
      data=solved.content,
      mime_type=media_type,
      transfer_url=transfer_url,
    )
    facts = {
      "container": solved.container,
      "codec": solved.video_codec,
      "duration_ms": solved.duration_ms,
      "width": solved.width,
      "height": solved.height,
    }
  else:
    return None

  context = {
    "request": "interpret media and submit only a useful additive graph",
    "focal_block": {
      "id": block.id,
      "resolver": block.resolver,
      "facts": facts,
    },
    "direct_relations": await _relation_context(typing.cast(int, block.id)),
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
      media,
    )
  )


async def interpret_missing_media() -> MediaInterpretationReport:
  config = await _config()
  if config is None:
    return MediaInterpretationReport()
  selected = interpreted = unavailable = failed = no_output = 0
  diagnostics: list[MediaInterpretationDiagnostic] = []

  def diagnostic(
    block: int,
    modality: typing.Literal["image", "audio", "video"],
    outcome: typing.Literal["unavailable", "failed", "no_output"],
    reason: str,
  ) -> None:
    if len(diagnostics) < _DIAGNOSTIC_LIMIT:
      diagnostics.append(
        MediaInterpretationDiagnostic(
          block=block,
          modality=modality,
          outcome=outcome,
          reason=reason,
        )
      )

  for block in await _candidates():
    selected += 1
    block_id = typing.cast(int, block.id)
    modality = _RESOLVER_MODALITIES[block.resolver]
    agent = _agent(config, modality)
    if not await AgentManager.can_execute(agent, modality):
      unavailable += 1
      diagnostic(block_id, modality, "unavailable", "agent_not_locally_executable")
      continue
    try:
      message = await _message(block)
      if message is None:
        unavailable += 1
        diagnostic(block_id, modality, "unavailable", "media_input_unavailable")
        continue
      thread = await AgentManager.run(agent, message)
      turn = thread.current_turn
      if turn is None:
        raise RuntimeError("Media interpretation Agent did not start a Turn")
      outcome = await turn
      if outcome == TurnTermination.MAX_MODEL_CALLS:
        raise RuntimeError("Media interpretation exceeded its model-call budget")
    except Exception as error:
      failed += 1
      logger.exception("Media interpretation failed", extra={"block": block_id})
      diagnostic(block_id, modality, "failed", type(error).__name__)
      continue
    if await get_related_block(block_id, content="interpretation") is None:
      no_output += 1
      diagnostic(block_id, modality, "no_output", "missing_interpretation_relation")
    else:
      interpreted += 1

  return MediaInterpretationReport(
    selected=selected,
    interpreted=interpreted,
    unavailable=unavailable,
    failed=failed,
    no_output=no_output,
    diagnostics=tuple(diagnostics),
  )
