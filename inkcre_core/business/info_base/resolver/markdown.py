"""Markdown source remains the exact text projection; rendering is a reader concern."""

from .inspection import decode_unicode_bytes
from .label import format_label
from .main import Resolver, TextProjectionContext


class MarkdownResolver(Resolver[str, str | bytes], rso_type="core.markdown.v1"):
  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> str:
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
    return await self.get_solved_content(
      refresh=refresh, materialize_missing=materialize_missing
    )

  async def get_label(self, *, refresh: bool = False) -> str:
    return format_label(
      "markdown",
      await self.get_text(refresh=refresh, materialize_missing=False),
      first_line=True,
    )
