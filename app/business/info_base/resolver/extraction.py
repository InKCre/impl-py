"""Read a retained extraction without requiring its original materials."""

import pydantic

from app.schemas.info_base.block import BlockID
from app.schemas.organization_extraction import (
  EXTRACTION_RESULT_RESOLVER,
  ExtractionContent,
  ExtractionSourceRelation,
)

from .contracts import ResolverContentError
from .label import format_label
from .main import Resolver, TextProjectionContext


class ExtractionResultResolver(
  Resolver[ExtractionContent, str | bytes], rso_type=EXTRACTION_RESULT_RESOLVER
):
  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> ExtractionContent:
    del materialize_missing
    content = await self.get_raw_content(refresh=refresh)
    try:
      return ExtractionContent.model_validate_json(content)
    except pydantic.ValidationError as error:
      raise ResolverContentError(self.__rsotype__, "invalid extraction content") from error

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del context, materialize_missing
    result = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    parts = [f"整理正文\n{result.text}", "原文依据"]
    for group in result.basis:
      parts.append(group.observed_origin.label)
      parts.extend(
        "\n".join(f"> {line}" for line in excerpt.split("\n")) for excerpt in group.excerpts
      )
    return "\n\n".join(parts)

  async def get_label(self, *, refresh: bool = False) -> str:
    result = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    return format_label("extraction", result.text, first_line=True)

  async def get_source(self, basis_key: str, *, refresh: bool = False) -> BlockID | None:
    """Resolve a live source; retained text remains readable if this link fails."""
    result = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    if basis_key not in {group.key for group in result.basis}:
      raise ValueError("Unknown extraction basis key")
    relations = await self.get_relations(
      refresh=refresh, include_in=True, include_out=False
    )
    sources: list[BlockID] = []
    try:
      for relation in relations:
        mapping = ExtractionSourceRelation.from_content(relation.content)
        if mapping is not None and mapping.basis_key == basis_key:
          sources.append(relation.from_)
    except pydantic.ValidationError as error:
      raise ResolverContentError(self.__rsotype__, "invalid source mapping") from error
    if len(sources) > 1:
      raise ResolverContentError(self.__rsotype__, "ambiguous source mapping")
    return sources[0] if sources else None
