"""Exact HTML-source resolver with a derived text projection."""

import asyncio

import html2text
from lxml import html as lxml_html

from .inspection import decode_html_bytes
from .label import format_label
from .main import Resolver, TextProjectionContext


class HTMLResolver(Resolver[str, str | bytes], rso_type="core.html.v1"):
  @classmethod
  def create_graph(cls, url: str):
    from app.schemas.info_base.block import BlockForm
    from app.schemas.info_base.main import StarsGraphForm

    return StarsGraphForm(
      block=BlockForm(resolver=cls.__rsotype__, content=url, storage=-1)
    )

  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del materialize_missing
    content = await self.get_raw_content(refresh=refresh)
    return (
      content if isinstance(content, str) else decode_html_bytes(content, self.__rsotype__)
    )

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
    if any(f"<{tag}" in source.lower() for tag in ("video", "audio", "track")):
      document = lxml_html.document_fromstring(source)
      for resource in document.xpath("//video | //audio | //source | //track"):
        for attribute in ("src", "poster"):
          if address := resource.get(attribute):
            link = lxml_html.Element("a", href=address)
            link.text = resource.get("label") or address
            resource.addnext(link)
      source = lxml_html.tostring(document, encoding="unicode")
    return await asyncio.to_thread(html2text.HTML2Text().handle, source)

  async def get_label(self, *, refresh: bool = False) -> str:
    source = await self.get_solved_content(
      refresh=refresh,
      materialize_missing=False,
    )
    identifier = None
    try:
      document = lxml_html.fromstring(source)
      values = document.xpath("//title/text()") or document.xpath(
        "//h1//text() | //h2//text() | //h3//text() | //h4//text() | "
        "//h5//text() | //h6//text()"
      )
      identifier = next((str(value) for value in values if str(value).strip()), None)
    except (TypeError, ValueError):
      pass
    return format_label("html", identifier)
