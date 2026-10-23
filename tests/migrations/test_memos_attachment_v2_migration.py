"""Memos migration round trips in their own historical PostgreSQL database."""

from tests.database import read_block, read_relations


import asyncio
import datetime
import json
import os
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
import pytest
import sqlalchemy
import sqlmodel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database_contract.connection import normalized_database_url, psycopg_database_url
from app.business.info_base.storage.postgresql import PostgreSQLBlobPointer
from tests.database import TestSession
from app.schemas.info_base.block import BlockModel
from app.schemas.info_base.relation import RelationModel
from app.schemas.info_base.storage import StorageBlobModel
from extensions.memos import Extension
from extensions.memos.family import AttachmentApplicationService, CanonicalAttachment


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)

PROJECT_ROOT = Path(__file__).parents[2]
V1 = "extensions.memos.attachment.v1"
V2 = "extensions.memos.attachment.v2"
ATTACHMENT_REVISION = "e1f4a5b6c7d8"
PREVIOUS_REVISION = "d0e3f4a5b6c7"


@pytest.fixture
def attachment_database(monkeypatch):
  """Own a legacy schema instead of downgrading the shared current-contract fixture."""
  owner_url = normalized_database_url(os.environ["MIGRATION_DATABASE_URL"])
  database = f"memos_migration_{uuid.uuid4().hex}"
  database_url = (
    sqlalchemy.engine.make_url(owner_url)
    .set(database=database)
    .render_as_string(hide_password=False)
  )
  engine = sqlalchemy.create_engine(database_url, poolclass=NullPool)
  async_engine = create_async_engine(database_url, poolclass=NullPool)
  config = Config(PROJECT_ROOT / "alembic.ini")
  config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
  with psycopg.connect(psycopg_database_url(owner_url), autocommit=True) as owner:
    owner.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
  try:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", database_url)
    monkeypatch.setattr("tests.database.TEST_ENGINE", engine)
    monkeypatch.setattr(
      "app.persistence.info_base.uow.AsyncSessionFactory",
      async_sessionmaker(async_engine, expire_on_commit=False),
    )
    command.upgrade(config, ATTACHMENT_REVISION)
    with engine.begin() as connection:
      connection.execute(
        sqlalchemy.text(
          "INSERT INTO inkcre.storage_types (id, description, config_schema) "
          "VALUES ('postgresql_binary', 'Migration fixture', '{}'::jsonb)"
        )
      )
      connection.execute(
        sqlalchemy.text(
          "INSERT INTO inkcre.storages (id, type, config) "
          "VALUES (-4, 'postgresql_binary', '{}'::jsonb)"
        )
      )
    yield config
  finally:
    engine.dispose()
    asyncio.run(async_engine.dispose())
    with psycopg.connect(psycopg_database_url(owner_url), autocommit=True) as owner:
      owner.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))


def test_seeded_v1_upgrade_and_downgrade_preserve_identity_blob_and_owner_slot(
  attachment_database,
):
  config = attachment_database
  command.downgrade(config, PREVIOUS_REVISION)

  with TestSession() as db_session:
    blob = StorageBlobModel(data=b"migration bytes")
    db_session.add(blob)
    db_session.flush()
    memo = BlockModel(resolver="extensions.memos.memo.v1", content="{}")
    db_session.add(memo)
    db_session.flush()
    canonical_v1 = {
      "filename": "migration.png",
      "media_type": "image/png",
      "size": len(blob.data),
      "created_at": "2026-08-01T08:00:00Z",
      "blob_id": str(blob.id),
    }
    attachment = BlockModel(
      resolver=V1,
      storage=-4,
      content=json.dumps(canonical_v1, separators=(",", ":")),
    )
    db_session.add(attachment)
    db_session.flush()
    assert memo.id is not None and attachment.id is not None
    relation = RelationModel(
      from_=memo.id,
      to_=attachment.id,
      content="attachment:0",
    )
    db_session.add(relation)
    db_session.commit()
    attachment_id = attachment.id
    memo_id = memo.id
    blob_id = blob.id

  command.upgrade(config, ATTACHMENT_REVISION)
  Extension._init_resolvers()
  with TestSession() as db_session:
    metadata = read_block(attachment_id, db_session)
    assert metadata is not None
    assert metadata.resolver == V2
    assert metadata.storage is None
    canonical = CanonicalAttachment.from_block_content(metadata.content)
    assert canonical.filename == "migration.png"
    content_relations = read_relations(
      attachment_id,
      include_in=False,
      include_out=True,
      content="content",
      db_session=db_session,
    )
    assert len(content_relations) == 1
    semantic_id = content_relations[0].to_
    semantic = read_block(semantic_id, db_session)
    assert semantic is not None
    assert semantic.resolver == "core.image.v1"
    assert semantic.storage == -4
    assert PostgreSQLBlobPointer.model_validate_json(semantic.content).blob_id == blob_id
    owner = read_relations(
      memo_id,
      include_in=False,
      include_out=True,
      content="attachment:0",
      db_session=db_session,
    )
    assert [(item.from_, item.to_) for item in owner] == [(memo_id, attachment_id)]
  assert asyncio.run(
    AttachmentApplicationService.download(attachment_id, "migration.png")
  ) == ("image/png", b"migration bytes")

  command.downgrade(config, PREVIOUS_REVISION)
  with TestSession() as db_session:
    metadata = read_block(attachment_id, db_session)
    assert metadata is not None
    assert metadata.resolver == V1
    assert metadata.storage == -4
    restored = json.loads(metadata.content)
    assert restored["blob_id"] == str(blob_id)
    assert db_session.get(StorageBlobModel, blob_id).data == b"migration bytes"  # type: ignore[union-attr]
    assert read_block(semantic_id, db_session) is None
    assert (
      read_relations(
        attachment_id,
        include_in=False,
        include_out=True,
        content="content",
        db_session=db_session,
      )
      == ()
    )
    owner = read_relations(
      memo_id,
      include_in=False,
      include_out=True,
      content="attachment:0",
      db_session=db_session,
    )
    assert [(item.from_, item.to_) for item in owner] == [(memo_id, attachment_id)]


def test_failed_downgrade_preserves_shared_semantic_content(attachment_database):
  config = attachment_database

  with TestSession() as db_session:
    blob = StorageBlobModel(data=b"shared")
    db_session.add(blob)
    db_session.flush()
    semantic = BlockModel(
      resolver="core.file.v1",
      storage=-4,
      content=PostgreSQLBlobPointer(blob_id=blob.id).model_dump_json(),
    )
    db_session.add(semantic)
    db_session.flush()
    canonical = CanonicalAttachment(
      filename="shared.bin",
      media_type="application/octet-stream",
      size=6,
      created_at=datetime.datetime(2026, 8, 1, 8, tzinfo=datetime.UTC),
    )
    metadata = [
      BlockModel(resolver=V2, content=canonical.to_block_content()),
      BlockModel(resolver=V2, content=canonical.to_block_content()),
    ]
    db_session.add_all(metadata)
    db_session.flush()
    assert semantic.id is not None and all(item.id is not None for item in metadata)
    for item in metadata:
      db_session.add(RelationModel(from_=item.id, to_=semantic.id, content="content"))
    db_session.commit()
    metadata_ids = tuple(item.id for item in metadata if item.id is not None)

  with TestSession() as db_session:
    original_blocks = []
    for block_id in (semantic.id, *metadata_ids):
      block = db_session.get(BlockModel, block_id)
      assert block is not None
      original_blocks.append(block.model_dump())
    original_relations = [
      relation.model_dump()
      for relation in db_session.exec(
        sqlmodel.select(RelationModel)
        .where(
          RelationModel.from_.in_(metadata_ids)  # pyrefly: ignore[missing-attribute]
        )
        .order_by(sqlmodel.col(RelationModel.id))
      ).all()
    ]

  with pytest.raises(RuntimeError, match="post-upgrade information"):
    command.downgrade(config, PREVIOUS_REVISION)

  with TestSession() as db_session:
    for block_id, original in zip(
      (semantic.id, *metadata_ids), original_blocks, strict=True
    ):
      block = db_session.get(BlockModel, block_id)
      assert block is not None
      assert block.model_dump() == original
    assert [
      relation.model_dump()
      for relation in db_session.exec(
        sqlmodel.select(RelationModel)
        .where(
          RelationModel.from_.in_(metadata_ids)  # pyrefly: ignore[missing-attribute]
        )
        .order_by(sqlmodel.col(RelationModel.id))
      ).all()
    ] == original_relations
    persisted_blob = db_session.get(StorageBlobModel, blob.id)
    assert persisted_blob is not None
    assert persisted_blob.data == b"shared"
