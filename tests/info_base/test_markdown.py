"""Unicode source remains readable without a browser or source Peer."""

import datetime
from unittest.mock import AsyncMock

from app.business.info_base.resolver import (
  MarkdownResolver,
  ResolverManager,
  register_core_resolvers,
)
from app.schemas.info_base.block import BlockModel


def test_markdown_keeps_source_and_unicode(async_runner, monkeypatch):
  register_core_resolvers()
  assert (
    ResolverManager.match_media_type("text/markdown; charset=utf-8") == "core.markdown.v1"
  )
  source = (
    "# 中文\n\n```html\n<node>& value\n  indented\n```\n[link](https://example.org)\n"
  )
  now = datetime.datetime.now(datetime.UTC)
  block = BlockModel(
    id=1,
    resolver="core.markdown.v1",
    storage=None,
    content=source,
    created_at=now,
    updated_at=now,
  )
  reader = ResolverManager.get(block)
  assert isinstance(reader, MarkdownResolver)
  assert async_runner.run(reader.get_text()) == source
  monkeypatch.setattr(
    BlockModel, "get_hydrated_content", AsyncMock(return_value=source.encode("utf-16"))
  )
  assert async_runner.run(reader.get_text(refresh=True)) == source
