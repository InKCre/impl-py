"""Read a writer's committed v1 graph through the Python distribution."""

import json
import os
from uuid import uuid4

import pytest
import sqlmodel

from extensions.capture import Extension
from inkcre_core.business.info_base.resolver import ResolverManager, register_core_resolvers
from inkcre_core.schemas.info_base.block import BlockForm, BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.info_base.storage import StorageBlobModel
from inkcre_core.business.info_base.storage import StorageManager
from tests.database import TestSession, read_block, seed_blocks


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires a migrated disposable database",
)


@pytest.fixture
def capture_graph():
  register_core_resolvers()
  Extension.load_decoders()
  with TestSession() as db:
    root, first, second, note = seed_blocks(
      [
        BlockForm(
          resolver="extensions.capture.capture.v1",
          content=json.dumps(
            {
              "entry": "selection",
              "captured_at": "2026-08-01T12:34:00Z",
              "submission_id": str(uuid4()),
              "payload_digest": "a" * 64,
            }
          ),
        ),
        *(
          BlockForm(resolver="core.text.v1", content=value)
          for value in ("甲", "乙", "笔记")
        ),
      ],
      db,
    )
    assert root.id and first.id and second.id and note.id
    ids = [root.id, first.id, second.id, note.id]
    db.commit()
  try:
    yield tuple(ids)
  finally:
    with TestSession() as db:
      db.connection().execute(
        sqlmodel.delete(BlockModel).where(sqlmodel.col(BlockModel.id).in_(ids))
      )
      db.commit()


def test_material_order_and_annotation_survive_reopening(capture_graph, async_runner):
  root, first, second, note = capture_graph
  with TestSession() as db:
    db.add_all(
      [
        RelationModel(from_=root, to_=note, content="annotation"),
        RelationModel(from_=root, to_=second, content="material:1"),
        RelationModel(from_=root, to_=first, content="material:0"),
      ]
    )
    db.commit()
  saved = read_block(root)
  assert saved is not None
  reader = ResolverManager.get(saved)
  text = async_runner.run(reader.get_text(materialize_missing=False))
  assert text.split("\n\n")[1:] == ["材料：\n甲", "材料：\n乙", "用户附注：\n笔记"]

  with TestSession() as db:
    db.connection().execute(
      sqlmodel.delete(RelationModel).where(
        sqlmodel.col(RelationModel.from_) == root,
        sqlmodel.col(RelationModel.content) == "material:0",
      )
    )
    db.commit()
  with pytest.raises(ValueError, match="材料顺序"):
    async_runner.run(reader.get_text(refresh=True, materialize_missing=False))


def test_material_limit_matches_the_capture_graph_contract(capture_graph, async_runner):
  root, first, *_ = capture_graph
  with TestSession() as db:
    db.add_all(
      [
        RelationModel(from_=root, to_=first, content=f"material:{index}")
        for index in range(129)
      ]
    )
    db.commit()

  saved = read_block(root)
  assert saved is not None
  with pytest.raises(ValueError, match="128"):
    async_runner.run(ResolverManager.get(saved).get_solved_content())

  with TestSession() as db:
    db.connection().execute(
      sqlmodel.delete(RelationModel).where(
        sqlmodel.col(RelationModel.from_) == root,
        sqlmodel.col(RelationModel.content) == "material:128",
      )
    )
    db.commit()
  solved = async_runner.run(ResolverManager.get(saved).get_solved_content())
  assert len(solved.materials) == 128


def test_screenshot_has_readable_text_and_keeps_image_facts_for_search(
  capture_graph, semantic_content_assets, async_runner
):
  root, *_ = capture_graph
  async_runner.run(StorageManager.setup_builtin_storages_async())
  with TestSession() as db:
    blob = StorageBlobModel(data=(semantic_content_assets / "image.png").read_bytes())
    db.add(blob)
    attachment, image = seed_blocks(
      [
        BlockForm(
          resolver="extensions.capture.attachment.v1",
          content='{"mime_type":"image/png","filename":"sketch.png"}',
        ),
        BlockForm(
          resolver="core.image.v1",
          storage=-4,
          content=json.dumps({"blob_id": str(blob.id)}),
        ),
      ],
      db,
    )
    assert attachment.id and image.id
    ids = [attachment.id, image.id]
    db.add_all(
      [
        RelationModel(from_=root, to_=attachment.id, content="material:0"),
        RelationModel(from_=attachment.id, to_=image.id, content="content"),
      ]
    )
    db.commit()
  try:
    saved = read_block(root)
    assert saved is not None
    reader = ResolverManager.get(saved)
    assert "材料：\nsketch.png" in async_runner.run(
      reader.get_text(materialize_missing=False)
    )
    lexical = async_runner.run(
      reader.get_text(context="lexical", materialize_missing=False)
    )
    assert "sketch.png" in lexical and "image/png" in lexical and "width" in lexical
    with TestSession() as db:
      unnamed = db.get_one(BlockModel, attachment.id)
      unnamed.content = '{"mime_type":"image/png"}'
      db.add(unnamed)
      db.commit()
    assert "材料：\nimage/png" in async_runner.run(
      reader.get_text(refresh=True, materialize_missing=False)
    )
  finally:
    with TestSession() as db:
      db.connection().execute(
        sqlmodel.delete(BlockModel).where(sqlmodel.col(BlockModel.id).in_(ids))
      )
      db.delete(db.get_one(StorageBlobModel, blob.id))
      db.commit()


def test_capture_needs_material_or_an_annotation(capture_graph, async_runner):
  root, _, _, note = capture_graph
  saved = read_block(root)
  assert saved is not None
  reader = ResolverManager.get(saved)
  with pytest.raises(ValueError, match="材料和附注不能同时为空"):
    async_runner.run(reader.get_solved_content())

  with TestSession() as db:
    db.add(RelationModel(from_=root, to_=note, content="annotation"))
    db.commit()
  reopened = read_block(root)
  assert reopened is not None
  text = async_runner.run(ResolverManager.get(reopened).get_text(materialize_missing=False))
  assert text.split("\n\n")[1:] == ["用户附注：\n笔记"]
