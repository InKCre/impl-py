"""Exact generic-file resolver for bytes without stronger semantics."""

import asyncio
from dataclasses import dataclass

from .contracts import TextProjectionContext, UnsupportedResolverCapability
from .inspection import (
  ByteContentFacts,
  detect_media_type,
  format_lexical_facts,
  require_bytes,
)
from .main import Resolver


@dataclass(frozen=True, slots=True)
class FileSolvedContent(ByteContentFacts):
  pass


def _inspect_file(content: bytes) -> FileSolvedContent:
  return FileSolvedContent(
    content=content,
    byte_size=len(content),
    detected_media_type=detect_media_type(content),
  )


class FileResolver(
  Resolver[FileSolvedContent, bytes],
  rso_type="core.file.v1",
):
  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> FileSolvedContent:
    del materialize_missing
    content = require_bytes(
      await self.get_raw_content(refresh=refresh),
      self.__rsotype__,
    )
    return await asyncio.to_thread(_inspect_file, content)

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    if context == "default":
      raise UnsupportedResolverCapability(self.__rsotype__, "text")
    del materialize_missing
    solved = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    return format_lexical_facts(
      "file",
      (("media type", solved.detected_media_type), ("bytes", solved.byte_size)),
    )

  async def get_label(self, *, refresh: bool = False) -> str:
    del refresh
    return "file"
