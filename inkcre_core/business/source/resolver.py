"""Use-time projection for lazy Source anchor Blocks."""

import pydantic

from inkcre_core.business.info_base.resolver.label import format_label
from inkcre_core.business.info_base.resolver.main import Resolver, TextProjectionContext


SOURCE_RESOLVER_ID = "core.source.v1"


class SourceContent(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  id: int
  type: str
  nickname: str | None = None


class SourceResolver(Resolver[SourceContent, str], rso_type=SOURCE_RESOLVER_ID):
  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> SourceContent:
    del materialize_missing
    return SourceContent.model_validate_json(await self.get_raw_content(refresh=refresh))

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del context
    source = await self.get_solved_content(
      refresh=refresh,
      materialize_missing=materialize_missing,
    )
    name = f" named {source.nickname}" if source.nickname else ""
    return f"Source {source.id}{name} uses {source.type}."

  async def get_label(self, *, refresh: bool = False) -> str:
    source = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    return format_label("source", source.nickname or source.type)
