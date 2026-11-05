"""Black-box proof for shared config persistence and row timestamp ownership."""

from inkcre_core.business.deployment_config import DeploymentConfigService


import os
from contextlib import asynccontextmanager
import time
import typing
import uuid

import fastapi
from fastapi.testclient import TestClient
import pydantic
import pytest
import sqlalchemy
import sqlmodel

from inkcre_core.business.deployment_config import DeploymentConfigManager
from app.engine import ASYNC_DB_ENGINE
from tests.database import TestSession
from app.routes.deployment_config import ROUTER
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)


class ProbeConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  name: str
  enabled: bool = True


SCHEMA_ID = "tests.deployment_config.probe.v1"
DeploymentConfigManager.register_schema(SCHEMA_ID, ProbeConfig)


@pytest.fixture
def client():
  @asynccontextmanager
  async def lifespan(app):
    try:
      yield
    finally:
      await ASYNC_DB_ENGINE.dispose()

  app = fastapi.FastAPI(lifespan=lifespan)
  app.include_router(ROUTER)
  with TestClient(app) as client:
    yield client


def _execute(
  db: sqlmodel.Session,
  statement: sqlalchemy.TextClause,
  params: dict[str, typing.Any],
) -> None:
  db.exec(statement, params=params)  # pyrefly: ignore[no-matching-overload]


def test_config_resource_replace_patch_read_and_explicit_failures(client):
  key = f"tests.i0.{uuid.uuid4().hex}"
  unknown_key = f"{key}.unknown"
  invalid_key = f"{key}.invalid"

  try:
    created = client.put(
      f"/configs/{key}",
      json={"schema": SCHEMA_ID, "value": {"name": "first"}},
    )
    assert created.status_code == 201
    assert created.json()["schema"] == SCHEMA_ID
    assert created.json()["value"] == {"name": "first", "enabled": True}

    assert client.get(f"/configs/{key}").json() == created.json()
    patched = client.patch(f"/configs/{key}", json={"name": "second"})
    assert patched.status_code == 200
    assert patched.json()["value"] == {"name": "second", "enabled": True}
    assert patched.json()["schema"] == SCHEMA_ID

    assert client.patch(f"/configs/{key}.missing", json={}).status_code == 404
    assert (
      client.put(
        f"/configs/{key}.unknown-write",
        json={"schema": "tests.unknown.v1", "value": {}},
      ).status_code
      == 422
    )
    assert client.delete(f"/configs/{key}").status_code == 204
    assert client.get(f"/configs/{key}").status_code == 404

    with TestSession() as db:
      _execute(
        db,
        sqlalchemy.text(
          """
          INSERT INTO inkcre.configs (key, schema, value)
          VALUES
            (:unknown_key, 'tests.unknown.v1', '{}'::jsonb),
            (:invalid_key, :schema, '{"enabled": true}'::jsonb)
          """
        ),
        params={
          "unknown_key": unknown_key,
          "invalid_key": invalid_key,
          "schema": SCHEMA_ID,
        },
      )
      db.commit()

    assert client.get(f"/configs/{unknown_key}").status_code == 200
    assert client.get(f"/configs/{invalid_key}").status_code == 200
  finally:
    with TestSession() as db:
      _execute(
        db,
        sqlalchemy.text("DELETE FROM inkcre.configs WHERE key = ANY(:keys)"),
        params={"keys": [key, unknown_key, invalid_key]},
      )
      db.commit()


def test_database_owned_timestamps_ignore_no_op_and_observe_row_changes(
  async_runner,
):
  key = f"tests.i0.{uuid.uuid4().hex}"
  block_ids: list[int] = []

  try:
    config = async_runner.run(
      DeploymentConfigService.replace(key, SCHEMA_ID, {"name": "first"})
    )
    with TestSession() as db:
      first = BlockModel(resolver="core.text.v1", content="first")
      second = BlockModel(resolver="core.text.v1", content="second")
      db.add(first)
      db.add(second)
      db.commit()
      db.refresh(first)
      db.refresh(second)
      assert first.id is not None and second.id is not None
      block_ids.extend((first.id, second.id))
      relation = RelationModel(
        from_=first.id,
        to_=second.id,
        content="related",
      )
      db.add(relation)
      db.commit()
      db.refresh(relation)
      assert relation.id is not None
      relation_id = relation.id
      block_updated_at = first.updated_at
      relation_updated_at = relation.updated_at

    with TestSession() as db:
      _execute(
        db,
        sqlalchemy.text("UPDATE inkcre.configs SET value = value WHERE key = :key"),
        params={"key": key},
      )
      _execute(
        db,
        sqlalchemy.text("UPDATE inkcre.blocks SET resolver = resolver WHERE id = :id"),
        params={"id": block_ids[0]},
      )
      _execute(
        db,
        sqlalchemy.text("UPDATE inkcre.relations SET content = content WHERE id = :id"),
        params={"id": relation_id},
      )
      db.commit()

    assert (
      async_runner.run(DeploymentConfigService.read(key)).updated_at == config.updated_at
    )  # type: ignore[union-attr]
    with TestSession() as db:
      assert db.get(BlockModel, block_ids[0]).updated_at == block_updated_at  # type: ignore[union-attr]
      assert db.get(RelationModel, relation_id).updated_at == relation_updated_at  # type: ignore[union-attr]

    time.sleep(0.02)
    patched = async_runner.run(DeploymentConfigService.patch(key, {"name": "second"}))
    with TestSession() as db:
      _execute(
        db,
        sqlalchemy.text(
          "UPDATE inkcre.blocks SET resolver = 'core.html.v1' WHERE id = :id"
        ),
        params={"id": block_ids[0]},
      )
      _execute(
        db,
        sqlalchemy.text("UPDATE inkcre.relations SET content = 'changed' WHERE id = :id"),
        params={"id": relation_id},
      )
      db.commit()

    assert patched.updated_at > config.updated_at
    with TestSession() as db:
      assert db.get(BlockModel, block_ids[0]).updated_at > block_updated_at  # type: ignore[union-attr]
      assert db.get(RelationModel, relation_id).updated_at > relation_updated_at  # type: ignore[union-attr]
  finally:
    with TestSession() as db:
      _execute(
        db,
        sqlalchemy.text("DELETE FROM inkcre.configs WHERE key = :key"),
        params={"key": key},
      )
      if block_ids:
        _execute(
          db,
          sqlalchemy.text("DELETE FROM inkcre.blocks WHERE id = ANY(:ids)"),
          params={"ids": block_ids},
        )
      db.commit()
