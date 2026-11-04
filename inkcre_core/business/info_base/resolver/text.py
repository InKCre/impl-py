"""Exact plain-text resolver."""

import pydantic

from .inspection import decode_unicode_bytes
from .label import format_label
from .main import Resolver, TextProjectionContext


class TextDraftInput(pydantic.BaseModel):
  """Resolver-native input for drafting one plain-text Block."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  text: str


class TextResolver(Resolver[str, str | bytes], rso_type="core.text.v1"):
  draft_description = "Create one ordinary plain-text semantic content Block."
  draft_input_model = TextDraftInput

  @classmethod
  def create_graph(cls, input: str | TextDraftInput):
    from inkcre_core.schemas.info_base.block import BlockForm
    from inkcre_core.schemas.info_base.main import StarsGraphForm

    text = input.text if isinstance(input, TextDraftInput) else input
    return StarsGraphForm(block=BlockForm(resolver=cls.__rsotype__, content=text))

  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del materialize_missing
    content = await self.get_raw_content(refresh=refresh)
    return (
      content
      if isinstance(content, str)
      else decode_unicode_bytes(content, self.__rsotype__)
    )

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del context
    return await self.get_solved_content(
      refresh=refresh,
      materialize_missing=materialize_missing,
    )

  async def get_label(self, *, refresh: bool = False) -> str:
    return format_label(
      "text",
      await self.get_text(refresh=refresh, materialize_missing=False),
      first_line=True,
    )
