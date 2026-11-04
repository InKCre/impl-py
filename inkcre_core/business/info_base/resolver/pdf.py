"""Exact PDF resolver using bounded structural inspection."""

import asyncio
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .contracts import ResolverContentError, TextProjectionContext
from .inspection import (
  ByteContentFacts,
  detect_media_type,
  format_lexical_facts,
  require_bytes,
)
from .label import format_label
from .main import Resolver


@dataclass(frozen=True, slots=True)
class PDFSolvedContent(ByteContentFacts):
  pdf_version: str | None
  page_count: int | None
  is_encrypted: bool
  title: str | None
  author: str | None


def _metadata_text(metadata: object, attribute: str) -> str | None:
  value = getattr(metadata, attribute, None)
  return value if isinstance(value, str) else None


def _inspect_pdf(content: bytes) -> PDFSolvedContent:
  try:
    reader = PdfReader(BytesIO(content), strict=True)
    encrypted = reader.is_encrypted
    metadata = None if encrypted else reader.metadata
    page_count = None if encrypted else len(reader.pages)
  except (PdfReadError, OSError, ValueError, TypeError) as error:
    raise ResolverContentError("core.pdf.v1", "invalid PDF") from error

  header = reader.pdf_header
  version = header.removeprefix("%PDF-") if header.startswith("%PDF-") else None
  return PDFSolvedContent(
    content=content,
    byte_size=len(content),
    detected_media_type=detect_media_type(content),
    pdf_version=version,
    page_count=page_count,
    is_encrypted=encrypted,
    title=_metadata_text(metadata, "title"),
    author=_metadata_text(metadata, "author"),
  )


def _extract_pdf_text(content: bytes) -> str | None:
  """Extract a bounded text-layer projection without treating metadata as body."""
  try:
    reader = PdfReader(BytesIO(content), strict=True)
    if reader.is_encrypted:
      return None
    parts: list[str] = []
    remaining = 1_000_000
    for page in reader.pages[:2_000]:
      text = page.extract_text() or ""
      if text.strip():
        selected = text[:remaining]
        parts.append(selected)
        remaining -= len(selected)
      if remaining <= 0:
        break
  except (PdfReadError, OSError, ValueError, TypeError) as error:
    raise ResolverContentError("core.pdf.v1", "invalid PDF text layer") from error
  joined = "\n\n".join(parts).strip()
  return joined or None


class PDFResolver(
  Resolver[PDFSolvedContent, bytes],
  rso_type="core.pdf.v1",
):
  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> PDFSolvedContent:
    del materialize_missing
    content = require_bytes(
      await self.get_raw_content(refresh=refresh),
      self.__rsotype__,
    )
    return await asyncio.to_thread(_inspect_pdf, content)

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str | None:
    del materialize_missing
    content = require_bytes(
      await self.get_raw_content(refresh=refresh),
      self.__rsotype__,
    )
    body = await asyncio.to_thread(_extract_pdf_text, content)
    if context == "default":
      return body
    solved = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    metadata = format_lexical_facts(
      "PDF",
      (
        ("media type", solved.detected_media_type),
        ("title", solved.title),
        ("author", solved.author),
        ("pages", solved.page_count),
        ("PDF version", solved.pdf_version),
        ("encrypted", solved.is_encrypted),
      ),
    )
    return f"{metadata}\n\n{body}" if body else metadata

  async def get_label(self, *, refresh: bool = False) -> str:
    solved = await self.get_solved_content(
      refresh=refresh,
      materialize_missing=False,
    )
    return format_label("PDF", solved.title)
