"""Opt-in proof against the declared PostgreSQL development contract."""

from tests.database import read_block, read_relations, seed_block, seed_relation


import datetime
import os
from pathlib import Path

import pytest
import sqlalchemy
import sqlmodel

from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.info_base.storage.postgresql import PostgreSQLBlobPointer
from tests.database import TestSession
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.info_base.storage import StorageBlobModel
from extensions.memos import Extension
from extensions.memos.family import (
  AttachmentApplicationService,
  CanonicalMemo,
  CanonicalMemoPatch,
  MemoApplicationService,
  MemoVisibility,
)
from extensions.memos.family.attachment import AttachmentGraph
from tests.extensions.runtime_support import publish_extension


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)

SEMANTIC_ASSETS = Path(__file__).parents[3] / "assets" / "semantic-content"


@pytest.fixture(scope="module", autouse=True)
def _generated_semantic_content_assets(semantic_content_assets: Path) -> None:
  assert semantic_content_assets == SEMANTIC_ASSETS


def _cleanup(tracked_block_ids: set[int], tracked_blob_ids: set) -> None:
  with TestSession() as db_session:
    for relation in db_session.exec(
      sqlmodel.select(RelationModel).where(
        sqlalchemy.or_(
          RelationModel.from_.in_(  # pyrefly: ignore[missing-attribute]
            tracked_block_ids
          ),
          RelationModel.to_.in_(  # pyrefly: ignore[missing-attribute]
            tracked_block_ids
          ),
        )
      )
    ).all():
      db_session.delete(relation)
    for block in db_session.exec(
      sqlmodel.select(BlockModel).where(
        BlockModel.id.in_(  # pyrefly: ignore[missing-attribute]
          tracked_block_ids
        )
      )
    ).all():
      db_session.delete(block)
    for blob in db_session.exec(
      sqlmodel.select(StorageBlobModel).where(
        StorageBlobModel.id.in_(  # pyrefly: ignore[missing-attribute]
          tracked_blob_ids
        )
      )
    ).all():
      db_session.delete(blob)
    db_session.commit()


def _track_attachment(solved, tracked_block_ids: set[int], tracked_blob_ids: set):
  tracked_block_ids.update((solved.block_id, solved.content_block_id))
  with TestSession() as db_session:
    content_block = read_block(solved.content_block_id, db_session)
    assert content_block is not None
    pointer = PostgreSQLBlobPointer.model_validate_json(content_block.content)
  tracked_blob_ids.add(pointer.blob_id)
  return pointer.blob_id


@pytest.fixture
def memo_client():
  from app.engine import ASYNC_DB_ENGINE

  published = publish_extension(
    Extension,
    {"personal_access_token": "memos_pat_" + "A" * 32},
    raise_server_exceptions=False,
    persist_config=True,
  )
  with published.client as client:
    try:
      yield client
    finally:
      assert client.portal is not None
      client.portal.call(ASYNC_DB_ENGINE.dispose)
      published.unpublish()


def test_saved_pat_replacement_and_revocation_apply_without_restart(memo_client):
  """An admitted Peer can edit config without invoking the running Host."""
  from inkcre_core.schemas.extension import ExtensionModel

  old_token = "memos_pat_" + "A" * 32
  new_token = "memos_pat_" + "B" * 32

  def authenticate(token: str):
    return memo_client.get(
      "/memos/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

  def save(token: str | None):
    with TestSession() as session:
      installed = session.get(ExtensionModel, "inkcre/memos")
      assert installed is not None
      installed.config = {"personal_access_token": token}
      session.add(installed)
      session.commit()

  assert authenticate(old_token).status_code == 200
  save(new_token)
  assert authenticate(old_token).status_code == 401
  assert authenticate(new_token).status_code == 200
  save(None)
  assert authenticate(new_token).status_code == 401
  assert memo_client.get("/memos/api/v1/instance/profile").status_code == 200


def test_connection_address_preserves_public_prefix_and_requires_peer_auth(memo_client):
  from inkcre_core.business.peer import PeerManager
  from app.middleware import create_peer_jwt
  from inkcre_core.schemas.peer import PeerModel
  from app.settings import settings

  client = memo_client
  assert client.portal is not None
  client.portal.call(PeerManager.register_self)
  with TestSession() as session:
    peer = session.get(PeerModel, settings.peer_id)
    assert peer is not None
    peer.config = {"http_public_base_url": "https://example.test/inkcre/"}
    session.add(peer)
    session.commit()

  assert client.get("/memos/connection").status_code == 401
  assert (
    client.get(
      "/memos/connection",
      headers={"Authorization": "Bearer memos_pat_" + "A" * 32},
    ).status_code
    == 401
  )
  headers = {"Authorization": f"Bearer {create_peer_jwt(settings.jwt_secret)}"}
  response = client.get("/memos/connection", headers=headers)
  assert response.status_code == 200
  assert response.json() == {"server_url": "https://example.test/inkcre/memos"}

  with TestSession() as session:
    peer = session.get(PeerModel, settings.peer_id)
    assert peer is not None
    peer.config = {}
    session.add(peer)
    session.commit()
  assert client.get("/memos/connection", headers=headers).status_code == 409


@pytest.mark.parametrize(
  ("filename", "media_type", "resolver_id"),
  (
    ("image.png", "image/png", "core.image.v1"),
    ("audio.wav", "audio/wav", "core.audio.v1"),
    ("video.mp4", "video/mp4", "core.video.v1"),
    ("document.pdf", "application/pdf", "core.pdf.v1"),
    ("book.epub", "application/epub+zip", "core.epub.v1"),
    ("archive.zip", "application/zip", "core.zip.v1"),
    ("unknown.bin", "application/x-inkcre-unknown", "core.file.v1"),
  ),
)
def test_memos_declared_media_type_selects_exact_semantic_content(
  async_runner,
  filename,
  media_type,
  resolver_id,
):
  Extension._init_resolvers()
  async_runner.run(StorageManager.setup_builtin_storages_async())
  tracked_block_ids: set[int] = set()
  tracked_blob_ids = set()

  try:
    solved = async_runner.run(
      AttachmentApplicationService.create(
        filename=filename,
        media_type=media_type,
        content=(SEMANTIC_ASSETS / filename).read_bytes(),
      )
    )
    _track_attachment(solved, tracked_block_ids, tracked_blob_ids)
    with TestSession() as db_session:
      metadata = read_block(solved.block_id, db_session)
      semantic = read_block(solved.content_block_id, db_session)
      assert metadata is not None and semantic is not None
      assert metadata.storage is None
      assert semantic.resolver == resolver_id
      assert semantic.storage == -4
      assert (
        read_relations(
          solved.block_id,
          include_in=False,
          include_out=True,
          content="content",
          db_session=db_session,
        )[0].to_
        == solved.content_block_id
      )
    assert async_runner.run(
      AttachmentApplicationService.download(solved.block_id, filename)
    ) == (media_type, (SEMANTIC_ASSETS / filename).read_bytes())
  finally:
    _cleanup(tracked_block_ids, tracked_blob_ids)


def test_orphan_attach_reorder_download_and_owned_removal_round_trip(
  async_runner,
):
  Extension._init_resolvers()
  async_runner.run(StorageManager.setup_builtin_storages_async())
  tracked_block_ids: set[int] = set()
  tracked_blob_ids = set()

  try:
    first = async_runner.run(
      AttachmentApplicationService.create(
        filename="first.png",
        media_type="image/png",
        content=b"first",
      )
    )
    second = async_runner.run(
      AttachmentApplicationService.create(
        filename="second.png",
        media_type="image/png",
        content=b"second",
      )
    )
    first_blob_id = _track_attachment(first, tracked_block_ids, tracked_blob_ids)
    second_blob_id = _track_attachment(second, tracked_block_ids, tracked_blob_ids)

    memo = async_runner.run(
      MemoApplicationService.create(
        CanonicalMemo(
          body="attachment proof",
          created_at=datetime.datetime(2026, 8, 1, 8, tzinfo=datetime.UTC),
          updated_at=datetime.datetime(2026, 8, 1, 8, tzinfo=datetime.UTC),
        ),
        attachment_ids=(first.block_id, second.block_id),
      )
    )
    tracked_block_ids.add(memo.block_id)
    assert memo.attachment_ids == (first.block_id, second.block_id)
    assert async_runner.run(
      AttachmentApplicationService.download(first.block_id, "first.png")
    ) == ("image/png", b"first")

    reordered = async_runner.run(
      MemoApplicationService.update(
        memo.block_id,
        CanonicalMemoPatch(body="attachment proof updated"),
        attachment_ids=(second.block_id, first.block_id),
      )
    )
    assert reordered.attachment_ids == (second.block_id, first.block_id)

    reduced = async_runner.run(
      MemoApplicationService.update(
        memo.block_id,
        None,
        attachment_ids=(second.block_id,),
      )
    )
    assert reduced.attachment_ids == (second.block_id,)

    with TestSession() as db_session:
      assert read_block(first.block_id, db_session) is None
      assert db_session.get(StorageBlobModel, first_blob_id) is None
      remaining_relations = tuple(
        db_session.exec(
          sqlmodel.select(RelationModel).where(
            RelationModel.from_ == memo.block_id,
            RelationModel.content.like(  # pyrefly: ignore[missing-attribute]
              "attachment:%"
            ),
          )
        ).all()
      )
      assert [(relation.content, relation.to_) for relation in remaining_relations] == [
        ("attachment:0", second.block_id)
      ]

    async_runner.run(AttachmentApplicationService.delete(second.block_id))
    with TestSession() as db_session:
      assert read_block(second.block_id, db_session) is None
      assert db_session.get(StorageBlobModel, second_blob_id) is None
  finally:
    _cleanup(tracked_block_ids, tracked_blob_ids)


def test_comment_visibility_and_owned_delete_preserve_shared_reference_targets(
  async_runner,
):
  Extension._init_resolvers()
  async_runner.run(StorageManager.setup_builtin_storages_async())
  tracked_block_ids: set[int] = set()
  tracked_blob_ids = set()

  async def attachment(name: str, content: bytes):
    solved = await AttachmentApplicationService.create(
      filename=name,
      media_type="image/png",
      content=content,
    )
    _track_attachment(solved, tracked_block_ids, tracked_blob_ids)
    return solved

  def canonical(body: str, visibility=MemoVisibility.PRIVATE):
    return CanonicalMemo(
      body=body,
      created_at=datetime.datetime(2026, 8, 1, 8, tzinfo=datetime.UTC),
      updated_at=datetime.datetime(2026, 8, 1, 8, tzinfo=datetime.UTC),
      visibility=visibility,
    )

  try:
    exclusive = async_runner.run(attachment("exclusive.png", b"exclusive"))
    shared = async_runner.run(attachment("shared.png", b"shared"))
    comment_owned = async_runner.run(attachment("comment.png", b"comment"))
    exclusive_blob_id = PostgreSQLBlobPointer.model_validate_json(
      read_block(exclusive.content_block_id).content  # type: ignore[union-attr]
    ).blob_id
    shared_blob_id = PostgreSQLBlobPointer.model_validate_json(
      read_block(shared.content_block_id).content  # type: ignore[union-attr]
    ).blob_id
    comment_blob_id = PostgreSQLBlobPointer.model_validate_json(
      read_block(comment_owned.content_block_id).content  # type: ignore[union-attr]
    ).blob_id

    target = async_runner.run(MemoApplicationService.create(canonical("target")))
    tracked_block_ids.add(target.block_id)
    parent = async_runner.run(
      MemoApplicationService.create(
        canonical("parent", MemoVisibility.PROTECTED),
        attachment_ids=(exclusive.block_id, shared.block_id),
      )
    )
    tracked_block_ids.add(parent.block_id)

    comment = async_runner.run(
      MemoApplicationService.create_comment(
        parent.block_id,
        canonical("comment", MemoVisibility.PUBLIC),
        attachment_ids=(comment_owned.block_id,),
      )
    )
    tracked_block_ids.add(comment.block_id)
    assert comment.parent_id == parent.block_id
    assert comment.canonical.visibility is MemoVisibility.PROTECTED

    nested = async_runner.run(
      MemoApplicationService.create_comment(
        comment.block_id,
        canonical("nested"),
      )
    )
    tracked_block_ids.add(nested.block_id)
    sibling = async_runner.run(
      MemoApplicationService.create_comment(
        parent.block_id,
        canonical("sibling"),
      )
    )
    tracked_block_ids.add(sibling.block_id)

    updated = async_runner.run(
      MemoApplicationService.update(
        comment.block_id,
        CanonicalMemoPatch(
          body="comment updated",
          visibility=MemoVisibility.PUBLIC,
        ),
      )
    )
    assert updated.canonical.body == "comment updated"
    assert updated.canonical.visibility is MemoVisibility.PROTECTED

    page = async_runner.run(MemoApplicationService.list_comments(parent.block_id, limit=10))
    assert page.total_size == 2
    assert {item.block_id for item in page.comments} == {
      comment.block_id,
      sibling.block_id,
    }
    assert all(item.parent_id == parent.block_id for item in page.comments)

    async_runner.run(MemoApplicationService.delete(sibling.block_id))
    with TestSession() as db_session:
      assert read_block(sibling.block_id, db_session) is None
      assert read_block(parent.block_id, db_session) is not None

    with TestSession() as db_session:
      seed_relation(
        parent.block_id,
        target.block_id,
        "reference",
        db_session,
      )
      seed_relation(
        target.block_id,
        shared.block_id,
        "attachment:0",
        db_session,
      )
      db_session.commit()

    async_runner.run(MemoApplicationService.delete(parent.block_id))
    with TestSession() as db_session:
      assert read_block(parent.block_id, db_session) is None
      assert read_block(comment.block_id, db_session) is None
      assert read_block(nested.block_id, db_session) is None
      assert read_block(exclusive.block_id, db_session) is None
      assert read_block(comment_owned.block_id, db_session) is None
      assert db_session.get(StorageBlobModel, exclusive_blob_id) is None
      assert db_session.get(StorageBlobModel, comment_blob_id) is None

      assert read_block(target.block_id, db_session) is not None
      assert read_block(shared.block_id, db_session) is not None
      assert db_session.get(StorageBlobModel, shared_blob_id) is not None
      surviving_owner = read_relations(
        target.block_id,
        include_in=False,
        include_out=True,
        content="attachment:0",
        db_session=db_session,
      )
      assert [(item.from_, item.to_) for item in surviving_owner] == [
        (target.block_id, shared.block_id)
      ]
  finally:
    _cleanup(tracked_block_ids, tracked_blob_ids)


def test_comment_http_round_trip_reads_committed_resolver_graph(
  memo_client,
):
  Extension._init_resolvers()
  client = memo_client
  assert client.portal is not None
  client.portal.call(StorageManager.setup_builtin_storages_async)
  headers = {"Authorization": "Bearer " + "memos_pat_" + "A" * 32}
  tracked_block_ids: set[int] = set()

  try:
    parent_response = client.post(
      "/memos/api/v1/memos",
      headers=headers,
      json={
        "content": "HTTP parent",
        "visibility": "PROTECTED",
        "createTime": "2026-08-01T08:00:00Z",
      },
    )
    assert parent_response.status_code == 200
    parent_id = int(parent_response.json()["name"].removeprefix("memos/"))
    tracked_block_ids.add(parent_id)

    comment_response = client.post(
      f"/memos/api/v1/memos/{parent_id}/comments",
      headers=headers,
      json={
        "content": "HTTP comment",
        "visibility": "PUBLIC",
        "createTime": "2026-08-01T08:01:00Z",
      },
    )
    assert comment_response.status_code == 200
    assert comment_response.json()["visibility"] == "PROTECTED"
    assert comment_response.json()["parent"] == f"memos/{parent_id}"
    comment_id = int(comment_response.json()["name"].removeprefix("memos/"))
    tracked_block_ids.add(comment_id)

    listed = client.get(
      f"/memos/api/v1/memos/{parent_id}/comments",
      headers=headers,
    )
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["memos"]] == [f"memos/{comment_id}"]

    patched = client.patch(
      f"/memos/api/v1/memos/{comment_id}",
      headers=headers,
      json={"content": "HTTP comment updated", "visibility": "PUBLIC"},
    )
    assert patched.status_code == 200
    assert patched.json()["content"] == "HTTP comment updated"
    assert patched.json()["visibility"] == "PROTECTED"
    assert patched.json()["parent"] == f"memos/{parent_id}"

    removed = client.delete(
      f"/memos/api/v1/memos/{comment_id}",
      headers=headers,
    )
    assert removed.status_code == 200
    assert (
      client.get(
        f"/memos/api/v1/memos/{parent_id}/comments",
        headers=headers,
      ).json()["memos"]
      == []
    )
    with TestSession() as db_session:
      assert read_block(comment_id, db_session) is None
      assert read_block(parent_id, db_session) is not None
  finally:
    _cleanup(tracked_block_ids, set())


def test_primary_delete_succeeds_when_best_effort_attachment_cleanup_fails(
  async_runner,
  monkeypatch,
):
  Extension._init_resolvers()
  async_runner.run(StorageManager.setup_builtin_storages_async())
  tracked_block_ids: set[int] = set()
  tracked_blob_ids = set()

  try:
    attachment = async_runner.run(
      AttachmentApplicationService.create(
        filename="residue.png",
        media_type="image/png",
        content=b"residue",
      )
    )
    attachment_blob_id = _track_attachment(
      attachment,
      tracked_block_ids,
      tracked_blob_ids,
    )
    memo = async_runner.run(
      MemoApplicationService.create(
        CanonicalMemo(
          body="primary delete proof",
          created_at=datetime.datetime(2026, 8, 1, 8, tzinfo=datetime.UTC),
          updated_at=datetime.datetime(2026, 8, 1, 8, tzinfo=datetime.UTC),
        ),
        attachment_ids=(attachment.block_id,),
      )
    )
    tracked_block_ids.add(memo.block_id)

    async def fail_cleanup(_cls, _attachment_id, _db_session):
      raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(
      AttachmentGraph,
      "delete_component",
      classmethod(fail_cleanup),
    )
    async_runner.run(MemoApplicationService.delete(memo.block_id))

    with TestSession() as db_session:
      assert read_block(memo.block_id, db_session) is None
      assert read_block(attachment.block_id, db_session) is not None
      assert db_session.get(StorageBlobModel, attachment_blob_id) is not None
  finally:
    _cleanup(tracked_block_ids, tracked_blob_ids)


def test_missing_raw_bytes_are_404_without_fabricating_attachment_success(
  memo_client,
):
  Extension._init_resolvers()
  client = memo_client
  assert client.portal is not None
  client.portal.call(StorageManager.setup_builtin_storages_async)
  headers = {"Authorization": "Bearer " + "memos_pat_" + "A" * 32}
  tracked_block_ids: set[int] = set()
  tracked_blob_ids = set()

  try:
    created = client.post(
      "/memos/api/v1/attachments",
      headers=headers,
      json={
        "filename": "missing.png",
        "type": "image/png",
        "content": "cmF3",
      },
    )
    assert created.status_code == 200
    attachment_id = int(created.json()["name"].removeprefix("attachments/"))
    tracked_block_ids.add(attachment_id)

    with TestSession() as db_session:
      metadata_block = read_block(attachment_id, db_session)
      assert metadata_block is not None
      content_relations = read_relations(
        attachment_id, include_in=False, content="content", db_session=db_session
      )
      assert len(content_relations) == 1
      content_block = read_block(content_relations[0].to_, db_session)
      assert content_block is not None
      assert content_block.id is not None
      tracked_block_ids.add(content_block.id)
      pointer = PostgreSQLBlobPointer.model_validate_json(content_block.content)
      tracked_blob_ids.add(pointer.blob_id)
      blob = db_session.get(StorageBlobModel, pointer.blob_id)
      assert blob is not None
      db_session.delete(blob)
      db_session.commit()

    missing = client.get(
      f"/memos/file/attachments/{attachment_id}/missing.png",
      headers=headers,
    )
    assert missing.status_code == 404
    with TestSession() as db_session:
      assert read_block(attachment_id, db_session) is not None
  finally:
    _cleanup(tracked_block_ids, tracked_blob_ids)


def test_unknown_component_resolver_returns_500_instead_of_partial_memo_success(
  memo_client,
):
  Extension._init_resolvers()
  client = memo_client
  assert client.portal is not None
  client.portal.call(StorageManager.setup_builtin_storages_async)
  headers = {"Authorization": "Bearer " + "memos_pat_" + "A" * 32}
  tracked_block_ids: set[int] = set()

  try:
    assert client.portal is not None
    memo = client.portal.call(
      MemoApplicationService.create,
      CanonicalMemo(
        body="unknown resolver proof",
        created_at=datetime.datetime(2026, 8, 1, 8, tzinfo=datetime.UTC),
        updated_at=datetime.datetime(2026, 8, 1, 8, tzinfo=datetime.UTC),
      ),
    )
    tracked_block_ids.add(memo.block_id)
    with TestSession() as db_session:
      unknown = seed_block(
        BlockModel(
          resolver="extensions.memos.unknown.future",
          content="opaque",
        ),
        db_session,
      )
      assert unknown.id is not None
      tracked_block_ids.add(unknown.id)
      seed_relation(
        memo.block_id,
        unknown.id,
        "attachment:0",
        db_session,
      )
      db_session.commit()

    response = client.get(
      "/memos/api/v1/memos",
      headers=headers,
      params={"filter": 'creator == "users/inkcre"'},
    )
    assert response.status_code == 500
  finally:
    _cleanup(tracked_block_ids, set())


def test_concurrent_memos_cannot_claim_the_same_attachment(async_runner):
  import asyncio
  from extensions.memos.family.attachment import AttachmentOwnershipError

  Extension._init_resolvers()
  async_runner.run(StorageManager.setup_builtin_storages_async())
  tracked_block_ids: set[int] = set()
  tracked_blob_ids = set()

  async def scenario():
    attachment = await AttachmentApplicationService.create(
      filename="ownership.txt", media_type="text/plain", content=b"exclusive"
    )
    _track_attachment(attachment, tracked_block_ids, tracked_blob_ids)
    results = await asyncio.gather(
      *(
        MemoApplicationService.create(
          CanonicalMemo(body=f"concurrent owner {index}", created_at=None, updated_at=None),
          attachment_ids=(attachment.block_id,),
        )
        for index in range(2)
      ),
      return_exceptions=True,
    )
    winners = [result for result in results if not isinstance(result, BaseException)]
    assert len(winners) == 1
    tracked_block_ids.add(winners[0].block_id)
    assert sum(isinstance(result, AttachmentOwnershipError) for result in results) == 1
    with TestSession() as db:
      owners = read_relations(
        attachment.block_id, include_out=False, content="attachment:0", db_session=db
      )
      assert len(owners) == 1 and owners[0].from_ == winners[0].block_id

  try:
    async_runner.run(scenario())
  finally:
    _cleanup(tracked_block_ids, tracked_blob_ids)
