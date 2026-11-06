"""Database protocol proof for atomic native identity graph batches."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
import os
from threading import Barrier
from typing import TypeVar
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest


RowT = TypeVar("RowT")


def fetch_one(cursor: psycopg.Cursor[RowT]) -> RowT:
  row = cursor.fetchone()
  assert row is not None, "The query must return a row"
  return row


def connect():
  url = os.environ["INKCRE_TEST_DATABASE_URL"].replace(
    "postgresql+psycopg://", "postgresql://"
  )
  return psycopg.connect(url)


@pytest.fixture
def scope():
  if not os.getenv("INKCRE_TEST_DATABASE_URL"):
    pytest.skip("requires an explicitly selected disposable migrated PostgreSQL runtime")
  namespace = f"test/source-batch/{uuid4()}"
  source_ids = []
  with connect() as conn:
    conn.execute(
      "INSERT INTO inkcre.sources_types (id, description, config_schema) "
      "VALUES (%s, 'batch test', '{}')",
      (namespace,),
    )
    for _ in range(2):
      source_ids.append(
        fetch_one(
          conn.execute(
            "INSERT INTO inkcre.sources (type, config, state) "
            "VALUES (%s, '{}', '{}') RETURNING id",
            (namespace,),
          )
        )[0]
      )
  yield namespace, source_ids
  with connect() as conn:
    anchors = [
      row[0]
      for row in conn.execute(
        "SELECT block FROM inkcre.sources WHERE id=ANY(%s) AND block IS NOT NULL",
        (source_ids,),
      ).fetchall()
    ]
    conn.execute("DELETE FROM inkcre.sources WHERE id = ANY(%s)", (source_ids,))
    conn.execute("DELETE FROM inkcre.sources_types WHERE id = %s", (namespace,))
    conn.execute(
      "DELETE FROM inkcre.blocks WHERE id IN "
      "(SELECT block FROM inkcre.collection_records WHERE namespace = %s)",
      (namespace,),
    )
    conn.execute("DELETE FROM inkcre.blocks WHERE id=ANY(%s)", (anchors,))


def record(key="video:1", **content):
  return {
    "key": key,
    "resolver": "tests.collection.video.v1",
    "content": {"id": "1", **content},
  }


def commit(  # noqa: PLR0913 — the helper mirrors the admitted RPC inputs.
  conn,
  namespace,
  source_id,
  records,
  *,
  expected=None,
  next_state=None,
  config=None,
  relations=None,
  root_key=None,
):
  sql = "SELECT inkcre.commit_source_batch(%s, %s, %s, %s, %s, %s, %s)"
  args = (
    source_id,
    namespace,
    Jsonb(config or {}),
    Jsonb(expected or {}),
    Jsonb(next_state or {}),
    Jsonb(records),
    Jsonb(relations or []),
  )
  if root_key is not None:
    sql = "SELECT inkcre.commit_source_batch(%s, %s, %s, %s, %s, %s, %s, %s)"
    args = (*args, root_key)
  return fetch_one(
    conn.execute(
      sql,
      args,
    )
  )[0]


def test_native_identity_merge_membership_and_atomic_state(scope):
  namespace, (first, second) = scope
  relations = [{"from_key": "folder:1", "to_key": "video:1", "content": "contains"}]
  with connect() as conn:
    result = commit(
      conn,
      namespace,
      first,
      [record(title="known", description="retain"), record("folder:1", name="A")],
      next_state={"page": 1},
      relations=relations,
    )
    video = result["blocks"]["video:1"]
    folder = result["blocks"]["folder:1"]
    conn.execute(
      "INSERT INTO inkcre.relations (from_, to_, content) "
      "VALUES (%s, %s, 'user:organized')",
      (video, folder),
    )
  with connect() as conn:
    again = commit(
      conn,
      namespace,
      second,
      [record(title="updated")],
      next_state={"page": 2},
      relations=relations * 2,
    )
    assert again["blocks"]["video:1"] == video
    assert again["state"] == {"page": 2}
    assert json.loads(
      fetch_one(conn.execute("SELECT content FROM inkcre.blocks WHERE id = %s", (video,)))[
        0
      ]
    ) == {"id": "1", "title": "updated", "description": "retain"}
    assert fetch_one(
      conn.execute("SELECT state FROM inkcre.sources WHERE id = %s", (second,))
    )[0] == {"page": 2}
    assert (
      fetch_one(
        conn.execute(
          "SELECT count(*) FROM inkcre.relations "
          "WHERE from_=%s AND to_=%s AND content='contains'",
          (folder, video),
        )
      )[0]
      == 1
    )
    assert (
      fetch_one(
        conn.execute(
          "SELECT count(*) FROM inkcre.relations "
          "WHERE from_=%s AND content='user:organized'",
          (video,),
        )
      )[0]
      == 1
    )


def test_root_pointer_commits_with_graph_state_and_unknown_root_rolls_back(scope):
  namespace, (first, second) = scope
  with connect() as conn:
    result = commit(
      conn,
      namespace,
      first,
      [record("root")],
      next_state={"page": 1},
      root_key="root",
    )
    root = result["blocks"]["root"]
    first_anchor = result["source_block"]
    assert first_anchor != root
    assert fetch_one(
      conn.execute("SELECT block,state FROM inkcre.sources WHERE id=%s", (first,))
    ) == (first_anchor, {"page": 1})
    second_result = commit(conn, namespace, second, [], root_key="root")
    second_anchor = second_result["source_block"]
    assert second_anchor not in {root, first_anchor}
    assert (
      fetch_one(conn.execute("SELECT block FROM inkcre.sources WHERE id=%s", (second,)))[0]
      == second_anchor
    )
    assert (
      fetch_one(
        conn.execute(
          "SELECT count(*) FROM inkcre.relations WHERE from_=ANY(%s) "
          "AND to_=%s AND content='collects'",
          ([first_anchor, second_anchor], root),
        )
      )[0]
      == 2
    )

  async def rename_source():
    from inkcre_core import Core
    from inkcre_core.business.source import SourceManager
    from inkcre_core.schemas.source import SourceUpdateForm

    core = Core(os.environ["INKCRE_TEST_DATABASE_URL"])
    try:
      with core.bind():
        await SourceManager.update(first, SourceUpdateForm(nickname="renamed"))
    finally:
      await core.aclose()

  asyncio.run(rename_source())
  with connect() as conn:
    anchor = fetch_one(
      conn.execute(
        "SELECT resolver,content FROM inkcre.blocks WHERE id=%s", (first_anchor,)
      )
    )
    assert anchor[0] == "core.source.v1"
    assert json.loads(anchor[1]) == {"id": first, "type": namespace, "nickname": "renamed"}
    assert (
      fetch_one(conn.execute("SELECT resolver FROM inkcre.blocks WHERE id=%s", (root,)))[0]
      == "tests.collection.video.v1"
    )
  with connect() as conn:
    with pytest.raises(psycopg.Error) as caught:
      with conn.transaction():
        commit(
          conn,
          namespace,
          first,
          [record("new")],
          expected={"page": 1},
          next_state={"page": 2},
          root_key="absent",
        )
    assert caught.value.sqlstate == "22023"
    assert fetch_one(
      conn.execute("SELECT block,state FROM inkcre.sources WHERE id=%s", (first,))
    ) == (first_anchor, {"page": 1})
    assert (
      fetch_one(
        conn.execute(
          "SELECT count(*) FROM inkcre.collection_records WHERE namespace=%s", (namespace,)
        )
      )[0]
      == 1
    )
    # Seven positional arguments still resolve to the one eight-argument function.
    commit(conn, namespace, first, [], expected={"page": 1}, next_state={"page": 2})
    assert fetch_one(
      conn.execute("SELECT block,state FROM inkcre.sources WHERE id=%s", (first,))
    ) == (first_anchor, {"page": 2})


def test_incompatible_source_anchor_never_rewrites_business_block(scope):
  namespace, (source_id, _) = scope
  with connect() as conn:
    result = commit(conn, namespace, source_id, [record(title="business")])
    video = result["blocks"]["video:1"]
    conn.execute("UPDATE inkcre.sources SET block=%s WHERE id=%s", (video, source_id))
  with connect() as conn:
    with pytest.raises(psycopg.Error) as caught:
      with conn.transaction():
        commit(
          conn,
          namespace,
          source_id,
          [record(title="must roll back")],
          root_key="video:1",
          next_state={"page": 1},
        )
    assert caught.value.sqlstate == "22023"
    stored = fetch_one(
      conn.execute("SELECT resolver,content FROM inkcre.blocks WHERE id=%s", (video,))
    )
    assert stored[0] == "tests.collection.video.v1"
    assert json.loads(stored[1])["title"] == "business"
    assert (
      fetch_one(conn.execute("SELECT state FROM inkcre.sources WHERE id=%s", (source_id,)))[
        0
      ]
      == {}
    )


@pytest.mark.parametrize("failure", ["state", "config", "resolver", "endpoint"])
def test_conflict_or_invalid_graph_rolls_back_entire_batch(scope, failure):
  namespace, (source_id, _) = scope
  with connect() as conn:
    initial = commit(
      conn, namespace, source_id, [record(title="before")], next_state={"page": 1}
    )
  with connect() as conn:
    with pytest.raises(psycopg.Error) as caught:
      with conn.transaction():
        changed = record(title="wrong")
        if failure == "resolver":
          changed["resolver"] = "tests.collection.incompatible.v1"
        commit(
          conn,
          namespace,
          source_id,
          [record("video:new"), changed],
          expected={} if failure == "state" else {"page": 1},
          config={"changed": True} if failure == "config" else {},
          next_state={"page": 2},
          relations=[{"from_key": "video:1", "to_key": "absent", "content": "contains"}]
          if failure == "endpoint"
          else [],
        )
    assert caught.value.sqlstate == ("PT409" if failure in {"state", "config"} else "22023")
    assert (
      fetch_one(
        conn.execute(
          "SELECT count(*) FROM inkcre.collection_records WHERE namespace=%s", (namespace,)
        )
      )[0]
      == 1
    )
    assert fetch_one(
      conn.execute("SELECT state FROM inkcre.sources WHERE id=%s", (source_id,))
    )[0] == {"page": 1}
    assert (
      json.loads(
        fetch_one(
          conn.execute(
            "SELECT content FROM inkcre.blocks WHERE id=%s", (initial["blocks"]["video:1"],)
          )
        )[0]
      )["title"]
      == "before"
    )


@pytest.mark.parametrize(
  "records",
  [
    [record(), record()],
    [record("")],
    [{"key": "bad", "resolver": "x", "content": []}],
    [record()] * 501,
  ],
)
def test_rejects_invalid_or_oversized_records(scope, records):
  namespace, (source_id, _) = scope
  with connect() as conn:
    with pytest.raises(psycopg.Error) as caught:
      with conn.transaction():
        commit(conn, namespace, source_id, records)
    assert caught.value.sqlstate == "22023"
    assert (
      fetch_one(
        conn.execute(
          "SELECT count(*) FROM inkcre.collection_records WHERE namespace=%s", (namespace,)
        )
      )[0]
      == 0
    )


def test_concurrent_sources_reuse_native_identity(scope):
  namespace, source_ids = scope
  barrier = Barrier(2)

  def execute(source_id):
    with connect() as conn:
      barrier.wait(timeout=10)
      return commit(
        conn, namespace, source_id, [record(title="same")], next_state={"done": True}
      )["blocks"]["video:1"]

  with ThreadPoolExecutor(max_workers=2) as pool:
    ids = list(pool.map(execute, source_ids))
  assert ids[0] == ids[1]
  with connect() as conn:
    assert (
      fetch_one(
        conn.execute(
          "SELECT count(*) FROM inkcre.collection_records WHERE namespace=%s", (namespace,)
        )
      )[0]
      == 1
    )


def test_rpc_is_invoker_and_does_not_grant_public_access(scope):
  with connect() as conn:
    row = fetch_one(
      conn.execute(
        "SELECT p.prosecdef, p.proconfig, "
        "EXISTS (SELECT 1 FROM aclexplode(p.proacl) acl "
        "WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE') "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='inkcre' AND p.proname='commit_source_batch'"
      )
    )
    assert row == (False, ["search_path=pg_catalog"], False)


def test_authenticated_executes_with_ordinary_permissions_and_anonymous_cannot(scope):
  namespace, source_ids = scope
  with connect() as conn:
    if (
      fetch_one(
        conn.execute(
          "SELECT count(*) FROM pg_roles WHERE rolname IN ('authenticated', 'anonymous')"
        )
      )[0]
      != 2
    ):
      pytest.skip("requires database lifecycle principals in the disposable cluster")
    # These grants are local to the disposable test database, not cluster role changes.
    conn.execute("GRANT USAGE ON SCHEMA inkcre TO authenticated")
    conn.execute(
      "GRANT SELECT,INSERT,UPDATE ON inkcre.sources, inkcre.blocks, "
      "inkcre.relations, inkcre.collection_records TO authenticated"
    )
    conn.execute("GRANT SELECT ON inkcre.storages, inkcre.storage_types TO authenticated")
    conn.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA inkcre TO authenticated")
    conn.execute(
      "GRANT EXECUTE ON FUNCTION inkcre.commit_source_batch"
      "(integer,text,jsonb,jsonb,jsonb,jsonb,jsonb,text) TO authenticated"
    )
    conn.execute("SET LOCAL ROLE authenticated")
    result = commit(conn, namespace, source_ids[0], [record()], next_state={"ok": True})
    assert result["blocks"]["video:1"] > 0
  with connect() as conn:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
      with conn.transaction():
        conn.execute("SET LOCAL ROLE anonymous")
        commit(conn, namespace, source_ids[1], [record("denied")])
    assert (
      fetch_one(
        conn.execute(
          "SELECT count(*) FROM inkcre.collection_records WHERE namespace=%s", (namespace,)
        )
      )[0]
      == 1
    )


def test_protocol_projection_declares_the_same_rpc_and_native_mapping():
  from app.database_contract.protocol import (
    protocol_database_function_signatures,
    protocol_document,
  )

  relations = protocol_document()["relations"]
  assert isinstance(relations, dict)
  assert "collection_records" in relations
  names, types, returns, returns_set, volatility = protocol_database_function_signatures()[
    "commit_source_batch"
  ]
  assert names == (
    "source_id",
    "namespace",
    "expected_config",
    "expected_state",
    "next_state",
    "records",
    "relations",
    "root_key",
  )
  assert types == ("integer", "text", "jsonb", "jsonb", "jsonb", "jsonb", "jsonb", "text")
  assert (returns, returns_set, volatility) == ("jsonb", False, "v")
