"""Read-only role/SQL whitelist regression, separate from mounted HTTP acceptance."""

import asyncio
import base64
import json
import os
import re
import uuid

from mcp.client import Client
from mcp.server import MCPServer
import pydantic
import pytest
import sqlalchemy
import sqlalchemy.event
import sqlmodel
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.business.sink.mcp import MCPSink
from app.database_contract.connection import normalized_database_url
from inkcre_core import Core
from inkcre_core.business.info_base.resolver import ResolverManager, register_core_resolvers
from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.source import SourceBase, SourceManager
from inkcre_core.schemas import Base
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.storage import StorageModel, StorageTypesModel
from inkcre_core.schemas.sink import SinkModel
from inkcre_core.schemas.source import SourceTypesModel
from tests.database import TEST_ENGINE

pytestmark = [
  pytest.mark.integration,
  pytest.mark.skipif(
    not os.getenv("INKCRE_TEST_DATABASE_URL") or not os.getenv("MIGRATION_DATABASE_URL"),
    reason="requires disposable PostgreSQL and explicit migration-owner URL",
  ),
]


@pytest.fixture
def discovery_database():
  schema = f"mcp_discovery_{uuid.uuid4().hex}"
  translation = {"inkcre": schema}
  with TEST_ENGINE.connect() as connection:
    runtime_role = connection.scalar(sqlalchemy.text("SELECT current_user"))
  engine = sqlalchemy.create_engine(
    normalized_database_url(os.environ["MIGRATION_DATABASE_URL"]),
    poolclass=NullPool,
    execution_options={"schema_translate_map": translation},
  )
  tables = [
    Base.metadata.tables[f"inkcre.{name}"]
    for name in ("storage_types", "storages", "blocks", "sources_types")
  ]
  try:
    with engine.begin() as connection:
      connection.execute(sqlalchemy.schema.CreateSchema(schema))
      Base.metadata.create_all(connection, tables=tables)
      quote = connection.dialect.identifier_preparer.quote_identifier
      connection.execute(
        sqlalchemy.text(f"GRANT USAGE ON SCHEMA {quote(schema)} TO {quote(runtime_role)}")
      )
      connection.execute(
        sqlalchemy.text(
          f"GRANT SELECT ON ALL TABLES IN SCHEMA {quote(schema)} TO {quote(runtime_role)}"
        )
      )
    yield engine, translation
  finally:
    try:
      with engine.begin() as connection:
        connection.execute(
          sqlalchemy.schema.DropSchema(schema, cascade=True, if_exists=True)
        )
    finally:
      engine.dispose()


def test_metadata_and_condition_pages_never_load_secrets_or_handlers(
  discovery_database, monkeypatch
):
  engine, translation = discovery_database
  monkeypatch.setattr(ResolverManager, "RESOLVER_CLS", {})
  monkeypatch.setattr(SourceManager, "_SOURCE_CLASSES", {})
  storage_classes = StorageManager._STORAGE_CLASSES.copy()
  monkeypatch.setattr(StorageManager, "_STORAGE_CLASSES", {})
  secret = uuid.uuid4().hex
  sink = MCPSink(SinkModel(id=1, type="core.mcp.v1", config={"pat": "test-only"}))
  server = MCPServer("metadata-isolation")
  sink._register_tools(server)

  async def scenario():
    read_engine = create_async_engine(
      TEST_ENGINE.url.render_as_string(hide_password=False),
      execution_options={"schema_translate_map": translation},
      connect_args={
        "options": "-c default_transaction_read_only=on -c statement_timeout=5000"
      },
    )
    core = Core(read_engine)
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
      statements.append(statement)

    sqlalchemy.event.listen(read_engine.sync_engine, "before_cursor_execute", capture)
    try:
      with core.bind():
        async with Client(server, mode="legacy") as client:

          async def call(tool, **arguments):
            response = await client.call_tool(tool, arguments)
            assert not response.is_error, response
            data = response.structured_content
            assert data is not None and secret not in json.dumps(data)
            return data

          collections = ("resolvers", "source_types", "storage_types", "storages")
          for collection in collections:
            assert (await call("meta_discover", collection=collection))["results"] == []
          register_core_resolvers()
          StorageManager._STORAGE_CLASSES.update(storage_classes)

          class Config(pydantic.BaseModel):
            token: str

          class LocalSource(SourceBase, config_cls=Config):
            """A locally registered collection implementation."""

            async def collect(self, job, config):
              raise AssertionError("Metadata must not collect")

          local_type = f"{LocalSource.__module__}.{LocalSource.__qualname__}"
          with engine.begin() as connection:
            connection.execute(
              sqlalchemy.insert(StorageTypesModel),
              [
                {
                  "id": "http",
                  "description": "HTTP bytes",
                  "config_schema": {"default": secret},
                },
                {
                  "id": "missing.package.Storage",
                  "description": "Retired" * 200,
                  "config_schema": {"default": secret},
                },
              ],
            )
            connection.execute(
              sqlalchemy.insert(StorageModel),
              [
                {"id": -4, "type": "http", "nickname": None, "config": {"pat": secret}},
                {
                  "id": -1,
                  "type": "missing.package.Storage",
                  "nickname": "Archive" * 100,
                  "config": {"password": secret},
                },
                {"id": 10, "type": "http", "nickname": "Remote", "config": {}},
              ],
            )
            connection.execute(
              sqlalchemy.insert(SourceTypesModel),
              [
                {
                  "id": local_type,
                  "description": "Local",
                  "config_schema": {"default": secret},
                },
                {
                  "id": "retired.Source",
                  "description": "Catalog only",
                  "config_schema": {},
                },
              ],
            )
            connection.execute(
              sqlalchemy.insert(BlockModel),
              [
                {
                  "id": 1,
                  "resolver": "core.text.v1",
                  "content": "Readable evidence",
                  "storage": None,
                },
                {
                  "id": 2,
                  "resolver": "legacy/unloaded",
                  "content": secret * 100000,
                  "storage": None,
                },
                {"id": 3, "resolver": "legacy/unloaded", "content": secret, "storage": -1},
              ],
            )
            connection.execute(
              sqlalchemy.update(BlockModel)
              .where(sqlmodel.col(BlockModel.id).in_([2, 3]))
              .values(created_at=None, updated_at=None)
            )

          def forbidden(*_args, **_kwargs):
            raise AssertionError(
              "Metadata/conditions must not construct or invoke handlers"
            )

          with monkeypatch.context() as guard:
            guard.setattr(ResolverManager, "get", forbidden)
            guard.setattr(StorageManager, "from_record", forbidden)
            guard.setattr(SourceManager, "get_source_ins", forbidden)
            rows_by_collection, cursors = {}, {}
            for collection in collections:
              rows, cursor = [], None
              statement_count = len(statements)
              while True:
                page = await call(
                  "meta_discover", collection=collection, limit=1, cursor=cursor
                )
                rows.extend(page["results"])
                cursor = page["next_cursor"]
                if cursor is None:
                  break
                cursors.setdefault(collection, cursor)
              if collection == "resolvers":
                assert len(statements) == statement_count
              rows_by_collection[collection] = rows
            assert [row["id"] for row in rows_by_collection["storages"]] == [-4, -1, 10]
            assert set(rows_by_collection["storages"][0]) == {"id", "type", "nickname"}
            assert len(rows_by_collection["storages"][1]["nickname"]) == 160
            assert len(rows_by_collection["storage_types"][1]["description"]) == 512
            assert [row["id"] for row in rows_by_collection["resolvers"]] == sorted(
              ResolverManager.RESOLVER_CLS
            )
            assert "core.image.v1" in {row["id"] for row in rows_by_collection["resolvers"]}
            assert "legacy/unloaded" not in {
              row["id"] for row in rows_by_collection["resolvers"]
            }
            assert {
              row["id"]: row["registered_locally"]
              for row in rows_by_collection["source_types"]
            } == {local_type: True, "retired.Source": False}
            assert {
              row["id"]: row["registered_locally"]
              for row in rows_by_collection["storage_types"]
            } == {"http": True, "missing.package.Storage": False}
            filters = {"resolver": {"eq": "legacy/unloaded"}}
            first = await call("query", filters=filters, limit=1)
            second = await call(
              "query", filters=filters, cursor=first["next_cursor"], limit=1
            )
            assert [row["entity"] for row in first["results"] + second["results"]] == [
              "block:2",
              "block:3",
            ]
            assert second["next_cursor"] is None
            assert all(row["updated_at"] is None for row in second["results"])
            for tool, arguments in (
              (
                "meta_discover",
                {"collection": "source_types", "cursor": cursors["storages"]},
              ),
              ("meta_discover", {"collection": "storages", "cursor": first["next_cursor"]}),
              ("query", {"filters": filters, "cursor": cursors["storages"]}),
              (
                "query",
                {"filters": {"storage": {"eq": -1}}, "cursor": first["next_cursor"]},
              ),
              ("query", {"filters": filters, "cursor": "not-a-cursor"}),
            ):
              failed = await client.call_tool(tool, arguments)
              assert failed.is_error
              assert failed.structured_content["error"]["code"] == "invalid_arguments"
            decoded = json.loads(base64.urlsafe_b64decode(first["next_cursor"]))
            for changes in ({"key": 2**100}, {"key": -(2**100)}, {"version": True}):
              cursor = base64.urlsafe_b64encode(
                json.dumps({**decoded, **changes}).encode()
              ).decode()
              failed = await client.call_tool(
                "query", {"filters": filters, "cursor": cursor}
              )
              assert failed.is_error

          assert statements and all(
            statement.lstrip().startswith("SELECT") for statement in statements
          )
          assert not any(
            re.search(
              r"\b(config|state|content|config_schema|collect_config_schema|backfill_config_schema)\b",
              statement,
            )
            for statement in statements
          )
          sqlalchemy.event.remove(read_engine.sync_engine, "before_cursor_execute", capture)
          opened = await call("open_entities", entities=["block:2"])
          assert opened["results"][0]["block"]["resolver"] == "legacy/unloaded"
          methods = await call("resolver_methods", blocks=[2])
          assert methods["errors"][0]["error"]["code"] == "unavailable"
          assert (await call("read_blocks", blocks=[1], content="solved"))["results"][0][
            "content"
          ] == "Readable evidence"
    finally:
      await core.aclose()
      await read_engine.dispose()

  asyncio.run(scenario())
