"""Real MCP/PG journey: zero-ID discovery, metadata isolation and continued reads.

A private schema in an explicitly disposable database makes empty catalogs observable.
The production reads run in PostgreSQL read-only transactions. SQL capture proves that
secret-bearing columns are not even loaded, beyond checking the public response.
"""

import asyncio
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

from app.business.sink.mcp import MCPSink
from inkcre_core import Core
from inkcre_core.business.info_base.resolver import ResolverManager, register_core_resolvers
from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.source import SourceBase, SourceManager
from inkcre_core.schemas import Base
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.storage import StorageModel, StorageTypesModel
from inkcre_core.schemas.sink import SinkModel
from inkcre_core.schemas.source import SourceModel, SourceTypesModel
from tests.database import TEST_ENGINE

pytestmark = [
  pytest.mark.integration,
  pytest.mark.skipif(
    not os.getenv("INKCRE_TEST_DATABASE_URL"),
    reason="requires an explicitly selected disposable PostgreSQL runtime",
  ),
]


@pytest.fixture
def discovery_database():
  schema = f"mcp_discovery_{uuid.uuid4().hex}"
  translation = {"inkcre": schema}
  engine = TEST_ENGINE.execution_options(schema_translate_map=translation)
  tables = [
    Base.metadata.tables[f"inkcre.{name}"]
    for name in ("storage_types", "storages", "blocks", "sources_types", "sources")
  ]
  with engine.begin() as connection:
    connection.execute(sqlalchemy.schema.CreateSchema(schema))
    Base.metadata.create_all(connection, tables=tables)
  try:
    yield engine, translation
  finally:
    with engine.begin() as connection:
      connection.execute(sqlalchemy.schema.DropSchema(schema, cascade=True))


def test_zero_id_discovery_is_bounded_safe_and_connects_to_existing_reads(
  discovery_database,
  monkeypatch,
):
  engine, translation = discovery_database
  monkeypatch.setattr(ResolverManager, "RESOLVER_CLS", {})
  monkeypatch.setattr(SourceManager, "_SOURCE_CLASSES", {})
  # Keep actual built-in handlers, but no instances are ever constructed.
  storage_classes = StorageManager._STORAGE_CLASSES.copy()
  monkeypatch.setattr(StorageManager, "_STORAGE_CLASSES", {})
  secret = uuid.uuid4().hex
  sink = MCPSink(SinkModel(id=1, type="core.mcp.v1", config={"pat": "test-only"}))
  server = MCPServer("zero-id-discovery")
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

          async def discover(collection, **arguments):
            response = await client.call_tool(
              "inkcre_discover",
              {"collection": collection, **arguments},
            )
            assert not response.is_error, response
            data = response.structured_content
            assert data is not None
            assert secret not in json.dumps(data)
            return data

          collections = (
            "resolvers",
            "persisted_resolvers",
            "source_types",
            "storage_types",
            "sources",
            "storages",
            "blocks",
          )
          for collection in collections:
            arguments = {"resolver": "legacy/unloaded"} if collection == "blocks" else {}
            empty = await discover(collection, **arguments)
            assert empty["results"] == []
            assert empty["next_cursor"] is None

          register_core_resolvers()
          StorageManager._STORAGE_CLASSES.update(storage_classes)

          class Config(pydantic.BaseModel):
            token: str

          class LocalSource(SourceBase, config_cls=Config):
            """A locally registered collection implementation."""

            async def collect(self, job, config):
              raise AssertionError("Discovery must not collect")

          local_type = f"{LocalSource.__module__}.{LocalSource.__qualname__}"
          # Real rows include catalog-only types and a missing import path.
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
            # Historical Block timestamps are nullable in the admitted schema,
            # including the lookahead row for a one-item page.
            connection.execute(
              sqlalchemy.update(BlockModel)
              .where(sqlmodel.col(BlockModel.id).in_([2, 3]))
              .values(created_at=None, updated_at=None)
            )
            connection.execute(
              sqlalchemy.insert(SourceModel),
              [
                {
                  "id": 1,
                  "type": local_type,
                  "nickname": "Inbox",
                  "config": {"token": secret},
                  "state": {"cursor": secret},
                  "storage": -4,
                  "block": 1,
                },
                {
                  "id": 2,
                  "type": "retired.Source",
                  "nickname": None,
                  "config": {"token": secret},
                  "state": {"cursor": secret},
                  "storage": None,
                  "block": None,
                },
              ],
            )

          def forbidden(*_args, **_kwargs):
            raise AssertionError("Discovery must not construct or invoke a handler")

          with monkeypatch.context() as guard:
            guard.setattr(ResolverManager, "get", forbidden)
            guard.setattr(StorageManager, "from_record", forbidden)
            guard.setattr(SourceManager, "get_source_ins", forbidden)
            all_rows = {}
            cursors = {}
            for collection in collections:
              arguments = {"resolver": "legacy/unloaded"} if collection == "blocks" else {}
              rows = []
              cursor = None
              while True:
                page = await discover(collection, limit=1, cursor=cursor, **arguments)
                assert len(page["results"]) <= 1
                rows.extend(page["results"])
                cursor = page["next_cursor"]
                if cursor is None:
                  break
                cursors.setdefault(collection, cursor)
              all_rows[collection] = rows
            assert [row["id"] for row in all_rows["storages"]] == [-4, -1, 10]
            assert len(all_rows["resolvers"]) >= 10
            assert {
              row["id"]: row["registered_locally"]
              for row in all_rows["persisted_resolvers"]
            } == {
              "core.text.v1": True,
              "legacy/unloaded": False,
            }
            assert {
              row["id"]: row["registered_locally"] for row in all_rows["source_types"]
            } == {
              local_type: True,
              "retired.Source": False,
            }
            assert {
              row["id"]: row["registered_locally"] for row in all_rows["storage_types"]
            } == {
              "http": True,
              "missing.package.Storage": False,
            }
            assert [row["registered_locally"] for row in all_rows["sources"]] == [
              True,
              False,
            ]
            assert all_rows["sources"][0]["block"] == 1
            assert all_rows["sources"][0]["storage"] == -4
            assert [row["entity"] for row in all_rows["blocks"]] == ["block:2", "block:3"]
            assert all(not row["registered_locally"] for row in all_rows["blocks"])
            assert all(
              row["created_at"] is None and row["updated_at"] is None
              for row in all_rows["blocks"]
            )
            assert len(all_rows["storages"][1]["nickname"]) == 160
            assert len(all_rows["storage_types"][1]["description"]) == 512
            assert (await discover("blocks", resolver="absent"))["results"] == []
            exact = await discover("blocks", resolver="legacy/unloaded", limit=2)
            assert len(exact["results"]) == 2 and exact["next_cursor"] is None

            # Cursor misuse fails instead of silently restarting or changing scope.
            for arguments in (
              {"collection": "sources", "cursor": cursors["storages"]},
              {
                "collection": "blocks",
                "resolver": "core.text.v1",
                "cursor": cursors["blocks"],
              },
              {"collection": "sources", "cursor": "not-a-cursor"},
              {"collection": "blocks"},
              {"collection": "sources", "resolver": "ignored"},
              {"collection": "sources", "limit": 101},
            ):
              failed = await client.call_tool("inkcre_discover", arguments)
              assert failed.is_error
              if "limit" not in arguments:
                assert failed.structured_content["error"]["code"] == "invalid_arguments"

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

          # The references work with existing tools; unloaded methods remain unavailable.
          opened = await client.call_tool("inkcre_open_entities", {"entities": ["block:2"]})
          assert not opened.is_error
          assert (
            opened.structured_content["results"][0]["block"]["resolver"]
            == "legacy/unloaded"
          )
          methods = await client.call_tool("inkcre_resolver_methods", {"blocks": [2]})
          assert methods.structured_content["errors"][0]["error"]["code"] == "unavailable"
          methods = await client.call_tool(
            "inkcre_resolver_methods", {"resolvers": ["core.text.v1"]}
          )
          assert methods.structured_content["results"][0]["methods"]
          read = await client.call_tool(
            "inkcre_read_blocks", {"blocks": [1], "content": "solved"}
          )
          assert not read.is_error
          assert read.structured_content["results"][0]["content"] == "Readable evidence"
    finally:
      await core.aclose()
      await read_engine.dispose()

  asyncio.run(scenario())
