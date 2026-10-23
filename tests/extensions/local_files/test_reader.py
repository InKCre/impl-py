"""Readers consume stored bodies; the source Peer and original file are unnecessary."""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from extensions.local_files.schema import FileMetadata, file_location
from extensions.local_files.resolver import LocalFileResolver
from app.business.info_base.services import BlockService


def test_location_retains_machine_path_without_remote_file_access():
  peer, path = file_location(
    "inkcre-peer-file://a143b185-f43d-47d1-8ef7-1d3857284b01/Users/a/%E4%B8%AD%E6%96%87%20%23%3F.md"
  )
  assert peer == UUID("a143b185-f43d-47d1-8ef7-1d3857284b01")
  assert path == "/Users/a/中文 #?.md"
  for value in [
    "file:///etc/passwd",
    "inkcre-peer-file://unknown/a",
    "inkcre-peer-file://a143b185-f43d-47d1-8ef7-1d3857284b01/a#",
  ]:
    with pytest.raises(ValueError):
      file_location(value)


def test_failed_current_file_keeps_successful_body_readable(monkeypatch, async_runner):
  metadata = FileMetadata(
    location="inkcre-peer-file://a143b185-f43d-47d1-8ef7-1d3857284b01/Users/a/note.docx",
    format="docx",
    available=False,
    observed_at=datetime.datetime.now(datetime.UTC),
    revision={
      "sha256": "a" * 64,
      "extractor": "mac-native-text.v1",
      "extracted_at": datetime.datetime.now(datetime.UTC),
      "scope": "main_document",
    },
    issue="当前原件损坏",
  )
  reader = object.__new__(LocalFileResolver)
  monkeypatch.setattr(
    reader, "get_raw_content", AsyncMock(return_value=metadata.model_dump_json())
  )
  monkeypatch.setattr(
    reader,
    "get_relations",
    AsyncMock(return_value=(SimpleNamespace(content="body", to_=20),)),
  )
  monkeypatch.setattr(
    BlockService,
    "get",
    AsyncMock(
      return_value=SimpleNamespace(
        id=20, resolver="core.text.v1", content="上次成功正文", storage=None
      )
    ),
  )
  text = async_runner.run(reader.get_text(materialize_missing=False))
  assert "上次成功正文" in text and "原件已失效" in text and "不含脚注" in text
  monkeypatch.setattr(reader, "get_relations", AsyncMock(return_value=()))
  with pytest.raises(ValueError, match="正文关系"):
    async_runner.run(reader.get_text())
