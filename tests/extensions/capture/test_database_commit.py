"""Direct admitted-database writes; no Python submission service participates."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import base64
import json
import os
import uuid
from typing import Any

import pytest
import sqlalchemy
from sqlalchemy.exc import DBAPIError

from tests.database import TestSession


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected disposable migrated PostgreSQL runtime",
)


def graph() -> dict[str, Any]:
  return {
    "root_local_id": -1,
    "blocks": [
      {
        "id": -1,
        "resolver": "extensions.capture.capture.v1",
        "content": json.dumps(
          {
            "captured_at": "2026-10-21T01:02:03Z",
            "entry": "paste",
            "origin": None,
          }
        ),
      },
      {"id": -2, "resolver": "core.text.v1", "content": "原文\n中文 👩🏽‍💻 e\u0301"},
      {
        "id": -3,
        "resolver": "extensions.capture.attachment.v1",
        "content": '{"mime_type":"image/png","filename":"截图.png"}',
      },
      {"id": -4, "resolver": "core.image.v1", "content": ""},
      {"id": -5, "resolver": "core.text.v1", "content": "自己的想法"},
    ],
    "relations": [
      {"from_": -1, "to_": -2, "content": "material:0"},
      {"from_": -1, "to_": -3, "content": "material:1"},
      {"from_": -3, "to_": -4, "content": "content"},
      {"from_": -1, "to_": -5, "content": "annotation"},
    ],
    "blobs": [
      {
        "block_id": -4,
        "data_base64": (
          "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAHCAIAAABPxRC5AAAAE0lEQVR4nGPk"
          "UbJgQAJMyBwa8AEvKAB0sOhdcgAAAABJRU5ErkJggg=="
        ),
      }
    ],
  }


def commit(identity, document, digest="a" * 64):
  with TestSession() as session:
    session.connection().execute(sqlalchemy.text("SET LOCAL ROLE authenticated"))
    result = (
      session.connection()
      .execute(
        sqlalchemy.text(
          "SELECT inkcre.commit_capture(:id, :digest, CAST(:graph AS jsonb))"
        ),
        {"id": identity, "digest": digest, "graph": json.dumps(document)},
      )
      .scalar_one()
    )
    session.commit()
    return result


def counts():
  with TestSession() as session:
    return tuple(
      session.connection()
      .execute(sqlalchemy.text(f"SELECT count(*) FROM inkcre.{name}"))
      .scalar_one()
      for name in ("blocks", "relations", "storage_blobs", "capture_receipts")
    )


@pytest.fixture
def submissions():
  identities = []
  yield identities
  with TestSession() as session:
    for identity in identities:
      root = (
        session.connection()
        .execute(
          sqlalchemy.text(
            "SELECT root_block_id FROM inkcre.capture_receipts WHERE submission_id=:id"
          ),
          {"id": identity},
        )
        .scalar_one_or_none()
      )
      if root is not None:
        ids = list(
          session.connection()
          .execute(
            sqlalchemy.text(
              "WITH RECURSIVE captured(id) AS (SELECT CAST(:root AS integer) UNION "
              "SELECT to_ FROM inkcre.relations JOIN captured ON from_=captured.id) "
              "SELECT id FROM captured"
            ),
            {"root": root},
          )
          .scalars()
        )
        pointers = list(
          session.connection()
          .execute(
            sqlalchemy.text(
              "SELECT content FROM inkcre.blocks WHERE id=ANY(:ids) AND storage=-4"
            ),
            {"ids": ids},
          )
          .scalars()
        )
        session.connection().execute(
          sqlalchemy.text("DELETE FROM inkcre.blocks WHERE id=ANY(:ids)"), {"ids": ids}
        )
        if pointers:
          session.connection().execute(
            sqlalchemy.text("DELETE FROM inkcre.storage_blobs WHERE id=ANY(:ids)"),
            {"ids": [uuid.UUID(json.loads(p)["blob_id"]) for p in pointers]},
          )
      session.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.capture_receipts WHERE submission_id=:id"),
        {"id": identity},
      )
    session.commit()


def test_concurrent_direct_commit_replay_conflict_and_deleted_root(submissions):
  identity = uuid.uuid4()
  submissions.append(identity)
  document = graph()
  before = counts()
  with ThreadPoolExecutor(max_workers=4) as executor:
    results = list(executor.map(lambda _: commit(identity, document), range(4)))
  assert all(result == results[0] for result in results)
  assert tuple(new - old for new, old in zip(counts(), before, strict=True)) == (5, 4, 1, 1)
  root = results[0]["root_block_id"]
  with TestSession() as session:
    blocks = list(
      session.connection()
      .execute(
        sqlalchemy.text(
          "WITH RECURSIVE captured(id) AS (SELECT CAST(:root AS integer) UNION "
          "SELECT to_ FROM inkcre.relations JOIN captured ON from_=captured.id) "
          "SELECT b.id,b.resolver,b.content,b.storage "
          "FROM inkcre.blocks b JOIN captured USING(id)"
        ),
        {"root": root},
      )
      .mappings()
    )
    image = next(block for block in blocks if block["resolver"] == "core.image.v1")
    assert image["storage"] == -4

    stored = (
      session.connection()
      .execute(
        sqlalchemy.text("SELECT inkcre.read_storage_blob(:id)"),
        {"id": uuid.UUID(json.loads(image["content"])["blob_id"])},
      )
      .scalar_one()
    )
    assert bytes(stored) == base64.b64decode(document["blobs"][0]["data_base64"])

  changed = deepcopy(document)
  changed["blocks"][4]["content"] = "changed graph with the same caller digest"
  with pytest.raises(DBAPIError) as conflict:
    commit(identity, changed)
  assert getattr(conflict.value.orig, "sqlstate", None) == "PT409"
  with pytest.raises(DBAPIError) as conflict:
    commit(identity, document, "b" * 64)
  assert getattr(conflict.value.orig, "sqlstate", None) == "PT409"

  # A delayed retry must not recreate the deleted test graph.
  with TestSession() as session:
    session.connection().execute(
      sqlalchemy.text("DELETE FROM inkcre.blocks WHERE id=ANY(:ids)"),
      {"ids": [block["id"] for block in blocks]},
    )
    session.connection().execute(
      sqlalchemy.text("DELETE FROM inkcre.storage_blobs WHERE id=:id"),
      {"id": uuid.UUID(json.loads(image["content"])["blob_id"])},
    )
    session.commit()
  before = counts()
  replay = commit(identity, document)
  assert replay["availability"] == "deleted" and replay["root_block_id"] == root
  assert counts() == before

  another_identity = uuid.uuid4()
  submissions.append(another_identity)
  another = commit(another_identity, document)
  assert another["root_block_id"] != root
  assert another["availability"] == "available"


@pytest.mark.parametrize("invalid_part", ["relation", "blob"])
def test_invalid_command_rolls_back_graph_and_original_bytes(invalid_part):
  document = graph()
  if invalid_part == "relation":
    document["relations"][-1]["to_"] = -999
  else:
    document["blobs"][0]["data_base64"] = "invalid!base64"
  before = counts()
  with pytest.raises(DBAPIError) as rejected:
    commit(uuid.uuid4(), document)
  assert getattr(rejected.value.orig, "sqlstate", None) == "PT422"
  assert counts() == before


def test_old_receipt_retains_original_identity_without_recreating_graph(submissions):
  identity = uuid.uuid4()
  submissions.append(identity)
  with TestSession() as session:
    root = (
      session.connection()
      .execute(
        sqlalchemy.text(
          "INSERT INTO inkcre.blocks (resolver,content) "
          "VALUES ('core.text.v1','old capture') RETURNING id"
        )
      )
      .scalar_one()
    )
    session.connection().execute(
      sqlalchemy.text(
        "INSERT INTO inkcre.capture_receipts(submission_id,payload_digest,root_block_id) "
        "VALUES (:id,:digest,:root)"
      ),
      {"id": identity, "digest": "a" * 64, "root": root},
    )
    session.commit()
  before = counts()
  assert commit(identity, graph())["root_block_id"] == root
  assert counts() == before
