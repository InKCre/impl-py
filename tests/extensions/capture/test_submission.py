"""Real HTTP boundary and PostgreSQL evidence for atomic capture intentions."""

import asyncio
import base64
from io import BytesIO
import json
import os
import uuid

import fastapi
import httpx
from PIL import Image
import pytest
import sqlalchemy

from app.middleware import create_peer_jwt
from app.persistence.capture.repository import CaptureReceiptRepository
from app.persistence.info_base.uow import graph_uow
from app.business.info_base.storage import StorageManager, WritableStorage
from app.schemas.info_base.block import BlockModel
from extensions.capture import Extension
from extensions.capture.resolver import CaptureResolver
from tests.database import TestSession


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected disposable migrated PostgreSQL runtime",
)


def application():
  app = fastapi.FastAPI()
  router = fastapi.APIRouter(prefix="/capture", dependencies=Extension.api_dependencies())
  Extension._register_apis(router)
  app.include_router(router)
  return app


def payload():
  image = BytesIO()
  Image.new("RGB", (5, 7), color=(12, 34, 56)).save(image, "PNG")
  return {
    "submission_id": str(uuid.uuid4()),
    "captured_at": "2026-10-21T01:02:03Z",
    "entry": "paste",
    "origin": {
      "application_id": "test.fixture",
      "title": "测试原文",
      "url": "https://example.test",
    },
    "materials": [
      {"kind": "text", "text": "原文\n中文 👩🏽‍💻 e\u0301"},
      {
        "kind": "image",
        "mime_type": "image/png",
        "data_base64": base64.b64encode(image.getvalue()).decode(),
      },
    ],
    "annotation": "自己的想法",
  }


def client():
  return httpx.AsyncClient(
    transport=httpx.ASGITransport(app=application(), raise_app_exceptions=False),
    base_url="http://capture.test",
    headers={
      "Authorization": f"Bearer {create_peer_jwt('test-only-jwt-secret-at-least-32-bytes')}"
    },
  )


def counts():
  with TestSession() as session:
    return tuple(
      session.connection()
      .execute(sqlalchemy.text(f"SELECT count(*) FROM inkcre.{name}"))
      .scalar_one()
      for name in ("blocks", "relations", "storage_blobs", "capture_receipts")
    )


def owned_blocks(root_id):
  with TestSession() as session:
    return set(
      session.connection()
      .execute(
        sqlalchemy.text(
          "WITH RECURSIVE captured(id) AS (SELECT CAST(:root AS integer) UNION "
          "SELECT to_ FROM inkcre.relations JOIN captured ON from_=captured.id "
          "WHERE content IN ('annotation', 'content') OR content LIKE 'material:%') "
          "SELECT id FROM captured"
        ),
        {"root": root_id},
      )
      .scalars()
    )


def cleanup(submissions, block_ids):
  with TestSession() as session:
    if block_ids:
      parameters = {"ids": list(block_ids)}
      pointers = list(
        session.connection()
        .execute(
          sqlalchemy.text(
            "SELECT content FROM inkcre.blocks WHERE id=ANY(:ids) AND storage=-4"
          ),
          parameters,
        )
        .scalars()
      )
      session.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.blocks WHERE id=ANY(:ids)"), parameters
      )
      if pointers:
        session.connection().execute(
          sqlalchemy.text("DELETE FROM inkcre.storage_blobs WHERE id=ANY(:ids)"),
          {"ids": [uuid.UUID(json.loads(pointer)["blob_id"]) for pointer in pointers]},
        )
    for identity in submissions:
      session.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.capture_receipts WHERE submission_id=:id"),
        {"id": identity},
      )
    session.commit()


def test_concurrent_replay_conflict_and_deleted_result(async_runner):
  body = payload()
  ids = [body["submission_id"]]
  block_ids = set()

  async def scenario():
    async with client() as http:
      before = counts()
      responses = await asyncio.gather(
        *(http.post("/capture/submit", json=body) for _ in range(4))
      )
      assert [response.status_code for response in responses] == [200] * 4
      results = [response.json() for response in responses]
      assert all(result == results[0] for result in results)
      assert tuple(new - old for new, old in zip(counts(), before, strict=True)) == (
        5,
        4,
        1,
        1,
      )
      root_id = results[0]["root_block_id"]
      block_ids.update(owned_blocks(root_id))
      async with graph_uow() as uow:
        root = await uow.blocks.get(root_id)
        images = [
          block
          for identity in block_ids
          if (block := await uow.blocks.get(identity)) is not None
          and block.resolver == "core.image.v1"
        ]
        assert len(images) == 1
        assert images[0].storage is not None
        storage = StorageManager.from_record(await uow.storage.get(images[0].storage))
        assert isinstance(storage, WritableStorage)
        assert await storage.read_content(
          images[0].content, uow.storage
        ) == base64.b64decode(body["materials"][1]["data_base64"])
      assert root is not None
      solved = await CaptureResolver(root).get_solved_content(materialize_missing=False)
      assert (
        solved.metadata.origin is not None and solved.metadata.origin.title == "测试原文"
      )
      assert len(solved.materials) == 2 and solved.annotation_block_id is not None

      conflict = await http.post("/capture/submit", json={**body, "annotation": "不同意图"})
      assert conflict.status_code == 409
      replay = await http.post("/capture/submit", json=body)
      assert replay.json() == results[0]
      with TestSession() as session:
        row = session.get(BlockModel, root_id)
        assert row is not None
        session.delete(row)
        session.commit()
      deleted_counts = counts()
      replay = await http.post("/capture/submit", json=body)
      assert replay.json()["availability"] == "deleted"
      assert replay.json()["root_block_id"] == root_id
      assert counts() == deleted_counts
      new_id = str(uuid.uuid4())
      ids.append(new_id)
      another = await http.post("/capture/submit", json={**body, "submission_id": new_id})
      assert another.status_code == 200 and another.json()["root_block_id"] != root_id
      block_ids.update(owned_blocks(another.json()["root_block_id"]))

  try:
    async_runner.run(scenario())
  finally:
    cleanup(ids, block_ids)


def test_receipt_failure_rolls_back_graph_and_image(async_runner, monkeypatch):
  body = payload()
  create_receipt = CaptureReceiptRepository.create

  async def rejected_receipt(self, submission_id, digest, root_id):
    # Exercise a real database constraint failure after writing graph and bytes.
    return await create_receipt(self, submission_id, "invalid-digest", root_id)

  monkeypatch.setattr(CaptureReceiptRepository, "create", rejected_receipt)

  async def scenario():
    async with client() as http:
      before = counts()
      response = await http.post("/capture/submit", json=body)
      assert response.status_code == 500
      assert counts() == before

  async_runner.run(scenario())


def test_rejected_payload_does_not_echo_private_material_or_write(async_runner):
  async def scenario():
    async with client() as http:
      before = counts()
      body = payload()
      body["materials"][0]["kind"] = "private-invalid-content"
      response = await http.post("/capture/submit", json=body)
      assert response.status_code == 422
      assert "自己的想法" not in response.text and "data_base64" not in response.text
      assert "private-invalid-content" not in response.text
      body = payload()
      body["materials"][1]["mime_type"] = "image/jpeg"
      response = await http.post("/capture/submit", json=body)
      assert response.status_code == 422
      assert counts() == before
      response = await http.post(
        "/capture/submit", json=payload(), headers={"Authorization": "Bearer invalid"}
      )
      assert response.status_code == 401

  async_runner.run(scenario())


def test_chunked_body_is_bounded_before_json_decoding(async_runner):
  async def oversized_body():
    for _ in range(49):
      yield b"x" * (1024 * 1024)

  async def scenario():
    async with client() as http:
      before = counts()
      response = await http.post(
        "/capture/submit",
        content=oversized_body(),
        headers={"Content-Type": "application/json"},
      )
      assert response.status_code == 413
      assert counts() == before

  async_runner.run(scenario())
