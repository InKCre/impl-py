"""Exact EPUB resolver using bounded package-document inspection."""

import asyncio
import datetime
from dataclasses import dataclass
from io import BytesIO
import zipfile

from lxml import etree

from .contracts import (
  ResolverContentError,
  TextProjectionContext,
  UnsupportedResolverCapability,
)
from .inspection import ByteContentFacts, format_lexical_facts, require_bytes
from .label import format_label
from .main import Resolver


_MAX_EPUB_MEMBERS = 10_000
_MAX_PACKAGE_BYTES = 2 * 1024 * 1024
_XML_PARSER = etree.XMLParser(
  resolve_entities=False,
  no_network=True,
  recover=False,
  huge_tree=False,
)


@dataclass(frozen=True, slots=True)
class EPUBSolvedContent(ByteContentFacts):
  epub_version: str | None
  title: str | None
  creators: tuple[str, ...]
  languages: tuple[str, ...]
  modified_at: datetime.datetime | None
  manifest_count: int
  spine_count: int
  has_navigation: bool


def _local_name(element: etree._Element) -> str:
  return etree.QName(element).localname


def _read_bounded(
  archive: zipfile.ZipFile,
  member_name: str,
  *,
  limit: int = _MAX_PACKAGE_BYTES,
) -> bytes:
  try:
    member = archive.getinfo(member_name)
  except KeyError as error:
    raise ResolverContentError(
      "core.epub.v1",
      f"missing required EPUB member {member_name}",
    ) from error
  if member.file_size > limit:
    raise ResolverContentError(
      "core.epub.v1",
      f"EPUB member {member_name} exceeds inspection limit",
    )
  with archive.open(member) as stream:
    result = stream.read(limit + 1)
  if len(result) > limit:
    raise ResolverContentError(
      "core.epub.v1",
      f"EPUB member {member_name} exceeds inspection limit",
    )
  return result


def _parse_xml(content: bytes, label: str) -> etree._Element:
  try:
    return etree.fromstring(content, parser=_XML_PARSER)
  except etree.XMLSyntaxError as error:
    raise ResolverContentError("core.epub.v1", f"invalid {label}") from error


def _first_text(elements: list[etree._Element]) -> str | None:
  for element in elements:
    if element.text and (value := element.text.strip()):
      return value
  return None


def _all_text(elements: list[etree._Element]) -> tuple[str, ...]:
  return tuple(
    value for element in elements if element.text and (value := element.text.strip())
  )


def _parse_modified(value: str | None) -> datetime.datetime | None:
  if value is None:
    return None
  try:
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError as error:
    raise ResolverContentError(
      "core.epub.v1",
      "invalid dcterms:modified timestamp",
    ) from error
  return parsed


def _inspect_epub(content: bytes) -> EPUBSolvedContent:
  try:
    with zipfile.ZipFile(BytesIO(content)) as archive:
      if len(archive.infolist()) > _MAX_EPUB_MEMBERS:
        raise ResolverContentError("core.epub.v1", "too many EPUB members")
      if _read_bounded(archive, "mimetype", limit=128) != b"application/epub+zip":
        raise ResolverContentError("core.epub.v1", "invalid EPUB mimetype member")

      container = _parse_xml(
        _read_bounded(archive, "META-INF/container.xml"),
        "EPUB container document",
      )
      rootfiles = [
        element for element in container.iter() if _local_name(element) == "rootfile"
      ]
      package_path = next(
        (element.get("full-path") for element in rootfiles if element.get("full-path")),
        None,
      )
      if package_path is None:
        raise ResolverContentError("core.epub.v1", "missing EPUB package path")
      package = _parse_xml(
        _read_bounded(archive, package_path),
        "EPUB package document",
      )
  except ResolverContentError:
    raise
  except (zipfile.BadZipFile, OSError, ValueError) as error:
    raise ResolverContentError("core.epub.v1", "invalid EPUB container") from error

  elements = list(package.iter())
  titles = [element for element in elements if _local_name(element) == "title"]
  creators = [element for element in elements if _local_name(element) == "creator"]
  languages = [element for element in elements if _local_name(element) == "language"]
  manifest_items = [element for element in elements if _local_name(element) == "item"]
  spine_items = [element for element in elements if _local_name(element) == "itemref"]
  modified = next(
    (
      element.text.strip()
      for element in elements
      if _local_name(element) == "meta"
      and element.get("property") == "dcterms:modified"
      and element.text
      and element.text.strip()
    ),
    None,
  )

  return EPUBSolvedContent(
    content=content,
    byte_size=len(content),
    detected_media_type="application/epub+zip",
    epub_version=package.get("version"),
    title=_first_text(titles),
    creators=_all_text(creators),
    languages=_all_text(languages),
    modified_at=_parse_modified(modified),
    manifest_count=len(manifest_items),
    spine_count=len(spine_items),
    has_navigation=any(
      "nav" in (element.get("properties") or "").split() for element in manifest_items
    ),
  )


class EPUBResolver(
  Resolver[EPUBSolvedContent, bytes],
  rso_type="core.epub.v1",
):
  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> EPUBSolvedContent:
    del materialize_missing
    content = require_bytes(
      await self.get_raw_content(refresh=refresh),
      self.__rsotype__,
    )
    return await asyncio.to_thread(_inspect_epub, content)

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
      "EPUB",
      (
        ("media type", solved.detected_media_type),
        ("title", solved.title),
        ("creators", ", ".join(solved.creators) or None),
        ("languages", ", ".join(solved.languages) or None),
        ("EPUB version", solved.epub_version),
        ("manifest items", solved.manifest_count),
        ("spine items", solved.spine_count),
      ),
    )

  async def get_label(self, *, refresh: bool = False) -> str:
    solved = await self.get_solved_content(
      refresh=refresh,
      materialize_missing=False,
    )
    return format_label("EPUB", solved.title)
