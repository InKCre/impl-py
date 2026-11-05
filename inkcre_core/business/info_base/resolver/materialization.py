"""Shared mechanics for Resolver-owned faithful text derivations."""

import logging

from inkcre_core.business.ai import AIManager
from inkcre_core.schemas.ai import SystemMessage, UserContentPart, UserMessage


logger = logging.getLogger(__name__)


async def generate_faithful_text(
  *,
  model: int,
  instruction: str,
  media: UserContentPart,
) -> str | None:
  """Ask one exact configured Model for source-faithful text, without graph access."""
  response = await AIManager.chat(
    model,
    (
      SystemMessage(content=instruction),
      UserMessage(content=(media,)),
    ),
  )
  text = (response.content or "").strip()
  return text or None


async def materialize_text_child(block_id: int, role: str, text: str) -> None:
  """Best-effort idempotent graph write for one exact information role."""
  from inkcre_core.schemas.info_base.main import (
    GraphBlockForm,
    GraphForm,
    GraphRelationForm,
  )

  from inkcre_core.business.info_base.commands import persist_graph
  from inkcre_core.persistence.info_base.uow import graph_uow

  # Recheck after provider work in the same short scope as the graph insertion.
  async with graph_uow() as uow:
    if await uow.blocks.get_related(block_id, content=role) is not None:
      return
    await persist_graph(
      GraphForm(
        blocks=(GraphBlockForm(id=-1, resolver="core.text.v1", content=text),),
        relations=(GraphRelationForm(from_=block_id, to_=-1, content=role),),
      ),
      uow,
    )


async def try_materialize_model_text(
  *,
  block_id: int,
  role: str,
  model: int,
  instruction: str,
  media: UserContentPart,
) -> None:
  """Keep one unavailable derivation shallow while preserving diagnostic evidence."""
  from inkcre_core.business.info_base.commands import get_related_block

  if await get_related_block(block_id, content=role) is not None:
    return
  try:
    text = await generate_faithful_text(
      model=model,
      instruction=instruction,
      media=media,
    )
    if text is not None:
      await materialize_text_child(block_id, role, text)
  except Exception:
    logger.warning(
      "Resolver text materialization unavailable",
      exc_info=True,
      extra={"block": block_id, "role": role, "model": model},
    )
