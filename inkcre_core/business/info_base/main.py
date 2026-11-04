"""Info-base graph command coordination and cross-retrieval use cases."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import typing

from inkcre_core.business.info_base.services import BlockService, get_entity_records
from inkcre_core.schemas.lexical_retrieval import LexicalRetrievalResult
from inkcre_core.schemas.semantic_retrieval import (
  SemanticRetrievalResult,
  VectorRetrievalOptions,
)
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel

from inkcre_core.schemas.info_base.main import (
  GraphBlockForm,
  GraphForm,
  GraphRelationForm,
  StarsGraphForm,
)


@dataclass(frozen=True)
class RetrievalBranch:
  result: LexicalRetrievalResult | SemanticRetrievalResult | None = None
  error: BaseException | None = None


class InfoBaseManager:
  """Own graph commands and use cases that span info-base read capabilities."""

  @classmethod
  async def retrieve(
    cls,
    query: str,
    mode: typing.Literal["lexical", "semantic", "hybrid"] = "hybrid",
    limit: int = 20,
  ) -> dict[str, RetrievalBranch]:
    """Run selected retrieval owners independently without merging their ranking."""
    from inkcre_core.business.lexical_retrieval import LexicalRetrievalManager
    from inkcre_core.business.semantic_retrieval import SemanticRetrievalManager

    operations: dict[str, typing.Awaitable] = {}
    if mode in {"lexical", "hybrid"}:
      operations["lexical"] = LexicalRetrievalManager.retrieve_local(query, limit)
    if mode in {"semantic", "hybrid"}:
      operations["semantic"] = SemanticRetrievalManager.retrieve_local(
        query, options=VectorRetrievalOptions(limit=limit)
      )
    outcomes = await asyncio.gather(*operations.values(), return_exceptions=True)
    return {
      name: RetrievalBranch(
        error=outcome if isinstance(outcome, BaseException) else None,
        result=None if isinstance(outcome, BaseException) else outcome,
      )
      for name, outcome in zip(operations, outcomes, strict=True)
    }

  @classmethod
  async def get_entities(
    cls,
    entities: typing.Collection[tuple[typing.Literal["block", "relation"], int]],
    *,
    random_count: int = 1,
  ) -> tuple[BlockModel | RelationModel | None, ...]:
    """Read ordered persisted entities, or random Blocks when selection is empty."""
    if not entities:
      return typing.cast(
        tuple[BlockModel | RelationModel | None, ...],
        await BlockService.get_random_many(random_count),
      )
    blocks, relations = await get_entity_records(
      tuple(identity for kind, identity in entities if kind == "block"),
      tuple(identity for kind, identity in entities if kind == "relation"),
    )
    return tuple(
      blocks.get(identity) if kind == "block" else relations.get(identity)
      for kind, identity in entities
    )

  @classmethod
  def normalize_graph(
    cls,
    stars: StarsGraphForm,
    id_start: int = -1,
  ) -> GraphForm:
    """Flatten recursive authoring while allocating deterministic negative IDs."""
    if id_start >= 0:
      raise ValueError("id_start must be negative")

    next_id = id_start
    blocks: list[GraphBlockForm] = []
    relations: list[GraphRelationForm] = []

    def visit(star: StarsGraphForm) -> int:
      nonlocal next_id
      block_id = next_id
      next_id -= 1
      blocks.append(GraphBlockForm.model_validate(star.block, update={"id": block_id}))

      for arc in star.out_arcs:
        to_id = visit(arc.to_graph)
        relations.append(
          GraphRelationForm.model_validate(
            arc.relation,
            update={"from_": block_id, "to_": to_id},
          )
        )
      for arc in star.in_arcs:
        from_id = visit(arc.from_graph)
        relations.append(
          GraphRelationForm.model_validate(
            arc.relation,
            update={"from_": from_id, "to_": block_id},
          )
        )
      return block_id

    visit(stars)
    return GraphForm(blocks=tuple(blocks), relations=tuple(relations))
