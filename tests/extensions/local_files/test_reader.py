"""Readers consume stored bodies independently of the original Peer."""

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from extensions.local_files import Extension
from extensions.local_files.schema import file_location
from inkcre_core.business.info_base.resolver import ResolverManager, register_core_resolvers
from inkcre_core.business.info_base.services import BlockService, RelationService
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel


LOCATION = "inkcre-peer-file://a143b185-f43d-47d1-8ef7-1d3857284b01"


def test_location_decodes_peer_and_absolute_path():
  peer, path = file_location(f"{LOCATION}/Users/a/%E4%B8%AD%E6%96%87%20%23%3F.md")
  assert peer == UUID("a143b185-f43d-47d1-8ef7-1d3857284b01")
  assert path == "/Users/a/中文 #?.md"
  for value, reason in [
    ("file:///etc/passwd", "Invalid Peer file location"),
    ("inkcre-peer-file://unknown/a", "UUID"),
    (f"{LOCATION}/a#", "Invalid Peer file location"),
  ]:
    with pytest.raises(ValueError, match=reason):
      file_location(value)


@pytest.fixture
def local_reader(monkeypatch):
  Extension.load_decoders()
  register_core_resolvers()

  def reader(version, metadata, body):
    monkeypatch.setattr(BlockService, "get", AsyncMock(return_value=body))
    edges = [RelationModel(from_=1, to_=20, content="body")] if body else []
    monkeypatch.setattr(RelationService, "get", AsyncMock(return_value=edges))
    return ResolverManager.get(
      BlockModel(
        id=1,
        resolver=f"extensions.local_files.file.v{version}",
        content=json.dumps(metadata),
      )
    )

  return reader


def test_failed_current_file_keeps_successful_body_readable(local_reader, async_runner):
  metadata = {
    "location": f"{LOCATION}/Users/a/note.docx",
    "format": "docx",
    "available": False,
    "observed_at": "2026-08-02T00:00:00Z",
    "revision": {
      "sha256": "a" * 64,
      "extractor": "mac-native-text.v1",
      "extracted_at": "2026-08-01T00:00:00Z",
      "scope": "main_document",
    },
    "issue": "当前原件损坏",
  }
  body = BlockModel(id=20, resolver="core.text.v1", content="上次成功正文")
  text = async_runner.run(
    local_reader(1, metadata, body).get_text(materialize_missing=False)
  )
  assert "上次成功正文" in text and "原件已失效" in text and "不含脚注" in text
  with pytest.raises(ValueError, match="正文关系"):
    async_runner.run(local_reader(1, metadata, None).get_text())


@pytest.mark.parametrize(
  "format_name,content,expected",
  [
    ("markdown", "# 标题\n\n`<node>`", "`<node>`"),
    (
      "html",
      '<h1>标题</h1><video src="https://example.org/video">'
      '<track src="https://example.org/captions.vtt"></video>',
      "https://example.org/captions.vtt",
    ),
  ],
)
def test_v2_uses_the_body_format(
  local_reader, async_runner, format_name, content, expected
):
  metadata = {
    "location": f"{LOCATION}/Users/a/note.{format_name}",
    "format": format_name,
    "available": True,
    "observed_at": "2026-08-02T00:00:00Z",
  }
  body = BlockModel(id=20, resolver=f"core.{format_name}.v1", content=content)
  assert expected in async_runner.run(
    local_reader(2, metadata, body).get_text(materialize_missing=False)
  )
  with pytest.raises(ValueError, match="类型不符"):
    async_runner.run(local_reader(1, metadata, body).get_text())
