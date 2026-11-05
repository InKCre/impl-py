"""Bounded implementation helpers shared by exact core content resolvers."""

from dataclasses import dataclass
from collections.abc import Iterable
import codecs
import re

import puremagic

from .contracts import ResolverContentError


@dataclass(frozen=True, slots=True)
class ByteContentFacts:
  """Peer-local actual bytes plus common byte-derived facts."""

  content: bytes
  byte_size: int
  detected_media_type: str | None


def detect_media_type(content: bytes) -> str | None:
  """Return bounded signature detection without inventing an unknown value."""
  try:
    detected = puremagic.from_string(content, mime=True)
  except puremagic.PureError:
    return None
  return detected if isinstance(detected, str) else None


def decode_unicode_bytes(content: bytes, resolver_id: str) -> str:
  """Decode a Unicode BOM when present, otherwise require strict UTF-8."""
  decoders = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
  )
  encoding = next(
    (candidate for bom, candidate in decoders if content.startswith(bom)),
    "utf-8",
  )
  try:
    return content.decode(encoding, errors="strict")
  except UnicodeError as error:
    raise ResolverContentError(resolver_id, f"invalid {encoding} text") from error


_HTML_CHARSET_SEARCH_BYTES = 4096
_HTML_CHARSET_PATTERNS = (
  re.compile(rb"<meta\s+[^>]*charset\s*=\s*['\"]?\s*([a-zA-Z0-9._-]+)", re.I),
  re.compile(rb"<meta\s+[^>]*content\s*=\s*['\"][^'\"]*charset=([a-zA-Z0-9._-]+)", re.I),
)


def decode_html_bytes(content: bytes, resolver_id: str) -> str:
  """Decode HTML using a Unicode BOM, bounded meta declaration, or strict UTF-8."""
  for bom in (
    codecs.BOM_UTF32_LE,
    codecs.BOM_UTF32_BE,
    codecs.BOM_UTF8,
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
  ):
    if content.startswith(bom):
      return decode_unicode_bytes(content, resolver_id)

  prefix = content[:_HTML_CHARSET_SEARCH_BYTES]
  declared_encoding = next(
    (
      match.group(1).decode("ascii")
      for pattern in _HTML_CHARSET_PATTERNS
      if (match := pattern.search(prefix)) is not None
    ),
    "utf-8",
  )
  try:
    encoding = codecs.lookup(declared_encoding).name
  except LookupError as error:
    raise ResolverContentError(
      resolver_id,
      f"unknown declared HTML charset {declared_encoding}",
    ) from error
  try:
    return content.decode(encoding, errors="strict")
  except UnicodeError as error:
    raise ResolverContentError(resolver_id, f"invalid {encoding} HTML") from error


def require_bytes(content: object, resolver_id: str) -> bytes:
  if not isinstance(content, bytes):
    raise ResolverContentError(resolver_id, "byte-oriented content must be storage-backed")
  return content


def format_lexical_facts(
  kind: str,
  facts: Iterable[tuple[str, object | None]],
) -> str:
  """Format bounded Resolver-owned metadata without serializing arbitrary objects."""
  lines = [kind]
  lines.extend(f"{label}: {value}" for label, value in facts if value is not None)
  return "\n".join(lines)
