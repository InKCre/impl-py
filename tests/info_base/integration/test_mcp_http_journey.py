"""Product journey over TCP: lifecycle/PAT → meta → exact query → open → read.

Graph corpus uses producer commands and PostgreSQL byte storage. Both real retrieval
owners maintain their own indices; only the graph-blind AI dialect is deterministic.
The private schema lives exclusively in the explicitly selected disposable database.
"""

import asyncio
from contextlib import asynccontextmanager
import logging
import os
import socket
import uuid

import fastapi
import httpx
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
import pydantic
import pytest
import sqlalchemy
import sqlmodel
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
import uvicorn

from app.business.sink import SinkManager
from app.business.sink.mcp import MCPSink
from app.core import CoreMiddleware
from app.database_contract.connection import normalized_database_url
from app.routes import lexical_retrieval, retrieval, semantic_retrieval
from inkcre_core import Core
from inkcre_core.business.ai import AIManager
from inkcre_core.business.ai.contracts import AIDialectAdapter
from inkcre_core.business.deployment_config import DeploymentConfigService
from inkcre_core.business.info_base.commands import submit_graph
from inkcre_core.business.info_base.resolver import ResolverManager, register_core_resolvers
from inkcre_core.business.info_base.resolver.text import TextResolver
from inkcre_core.business.info_base.storage import StorageManager, WritableStorage
from inkcre_core.business.lexical_retrieval import LexicalRetrievalManager
from inkcre_core.business.semantic_retrieval import SemanticRetrievalManager
from inkcre_core.business.source import SourceBase, SourceManager
from inkcre_core.persistence.info_base.uow import graph_uow
from inkcre_core.schemas import Base
from inkcre_core.schemas.ai import (
  AIModelModel,
  AIProviderModel,
  EmbeddingCapability,
  EmbeddingProfileModel,
)
from inkcre_core.schemas.info_base import GraphBlockForm, GraphForm, GraphRelationForm
from inkcre_core.schemas.info_base.query import BlockQueryFilters
from inkcre_core.schemas.lexical_retrieval import LexicalRetrievalRequest
from inkcre_core.schemas.semantic_retrieval import (
  SemanticRetrievalRequest,
  VectorRetrievalOptions,
)
from tests.database import TEST_ENGINE

pytestmark = [
  pytest.mark.integration,
  pytest.mark.skipif(
    not os.getenv("INKCRE_TEST_DATABASE_URL") or not os.getenv("MIGRATION_DATABASE_URL"),
    reason="requires disposable PostgreSQL and explicit migration-owner URL",
  ),
]

TOOLS = {
  "meta_discover",
  "query",
  "open_entities",
  "read_blocks",
  "expand_entities",
  "find_path",
  "resolver_methods",
  "invoke_resolver_methods",
}
NEEDLE = "quartzneedle"


@pytest.fixture
def journey_database():
  schema = f"mcp_journey_{uuid.uuid4().hex}"
  translation = {"inkcre": schema}
  with TEST_ENGINE.connect() as connection:
    runtime_role = connection.scalar(sqlalchemy.text("SELECT current_user"))
  owner = sqlalchemy.create_engine(
    normalized_database_url(os.environ["MIGRATION_DATABASE_URL"]),
    poolclass=NullPool,
    execution_options={"schema_translate_map": translation},
  )
  try:
    with owner.begin() as connection:
      connection.execute(sqlalchemy.schema.CreateSchema(schema))
      Base.metadata.create_all(connection)
      quote = connection.dialect.identifier_preparer.quote_identifier
      for grant in (
        f"USAGE ON SCHEMA {quote(schema)}",
        f"ALL ON ALL TABLES IN SCHEMA {quote(schema)}",
        f"USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {quote(schema)}",
      ):
        connection.execute(sqlalchemy.text(f"GRANT {grant} TO {quote(runtime_role)}"))
    yield owner, translation
  finally:
    try:
      with owner.begin() as connection:
        connection.execute(
          sqlalchemy.schema.DropSchema(schema, cascade=True, if_exists=True)
        )
    finally:
      owner.dispose()


def test_mounted_http_metadata_query_read_and_prefiltered_ranking(
  journey_database, monkeypatch, caplog
):
  owner, translation = journey_database
  # Isolate process-local registries/resources, not domain behavior.
  monkeypatch.setattr(SinkManager, "_running", {})
  monkeypatch.setattr(SinkManager, "_locks", {})
  monkeypatch.setattr(SinkManager, "_app", None)
  monkeypatch.setattr(SinkManager, "_SINK_CLASSES", {"core.mcp.v1": MCPSink})
  monkeypatch.setattr(ResolverManager, "RESOLVER_CLS", {})
  monkeypatch.setattr(SourceManager, "_SOURCE_CLASSES", {})
  monkeypatch.setattr(AIManager, "_DIALECTS", {})
  register_core_resolvers()

  class DistractorResolver(TextResolver, rso_type="test.distractor.v1"):
    """A second text identity with highly ranked nonmatching Blocks."""

  class Config(pydantic.BaseModel):
    pass

  class TestSource(SourceBase, config_cls=Config):
    """Journey source type; collection is not invoked by discovery."""

    async def collect(self, job, config):
      raise AssertionError("Discovery must not collect")

  calls = []

  @AIManager.register_dialect(
    "test.mcp-journey.v1", description="Deterministic vectors", config_model=Config
  )
  class DeterministicEmbedding(AIDialectAdapter):
    supported_input_modalities = {"embedding": frozenset({"text"})}

    async def embed(self, config, native_model_id, inputs, dimensions):
      assert dimensions == 3
      calls.extend(inputs)

      def vector(text):
        if text == NEEDLE or "property:\n" in text:
          return (1.0, 0.0, 0.0)
        if "Target evidence" in text:
          return (0.6, 0.8, 0.0)
        if NEEDLE in text:
          return (0.99, 0.1, 0.0)
        return (0.0, 0.0, 1.0)

      return tuple(vector(text) for text in inputs)

    async def chat(self, *args, **kwargs):
      raise AssertionError("No chat in discovery/query")

  async def scenario():
    engine = create_async_engine(
      TEST_ENGINE.url.render_as_string(hide_password=False),
      execution_options={"schema_translate_map": translation},
    )
    core = Core(engine)
    pat = uuid.uuid4().hex
    server_task = None
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    ready = asyncio.Event()

    class ReadyServer(uvicorn.Server):
      async def startup(self, sockets=None):
        await super().startup(sockets)
        ready.set()

    @asynccontextmanager
    async def lifespan(app):
      with core.bind():
        await SinkManager.startup(app, core.options.peer_id)
        try:
          yield
        finally:
          await SinkManager.shutdown()

    app = fastapi.FastAPI(lifespan=lifespan)
    app.add_middleware(CoreMiddleware, core=core)
    # Exercise existing ordinary and inbound forwarding with the same real owners.
    app.include_router(retrieval.ROUTER)
    app.include_router(lexical_retrieval.ROUTER)
    app.include_router(semantic_retrieval.ROUTER)
    server = ReadyServer(uvicorn.Config(app, log_level="error", lifespan="on"))
    # Uvicorn's normal handler does not propagate to pytest's root log capture.
    transport_logger = logging.getLogger("uvicorn.error")
    transport_logger.addHandler(caplog.handler)
    try:
      with core.bind():
        await StorageManager.setup_builtin_storages_async()
        await SourceManager.sync_source_types_async()
        await AIManager.sync_dialects_async()
        # AI registry facts are fixtures, not graph/retrieval-index inserts.
        with owner.begin() as connection:
          provider = connection.execute(
            sqlalchemy.insert(AIProviderModel)
            .values(name="MCP journey", dialect="test.mcp-journey.v1", config={})
            .returning(sqlmodel.col(AIProviderModel.id))
          ).scalar_one()
          model = connection.execute(
            sqlalchemy.insert(AIModelModel)
            .values(
              provider=provider,
              native_model_id="deterministic",
              capabilities=(
                EmbeddingCapability(
                  input_modalities=["text"], output_modalities=["vector"]
                ),
              ),
            )
            .returning(sqlmodel.col(AIModelModel.id))
          ).scalar_one()
          profile = connection.execute(
            sqlalchemy.insert(EmbeddingProfileModel)
            .values(name="MCP journey", ai_model=model, dimensions=3)
            .returning(sqlmodel.col(EmbeddingProfileModel.id))
          ).scalar_one()
        await DeploymentConfigService.replace(
          "semantic_retrieval",
          "core.semantic_retrieval.config.v1",
          {"default_profile": profile},
        )
        storages = await StorageManager.discover_instances(limit=100)
        storage_id = next(
          row["id"] for row in storages if row["type"] == "postgresql_binary"
        )
        assert storage_id < 0
        storage = await StorageManager.get_storage_async(storage_id)
        assert isinstance(storage, WritableStorage)
        contents = [f"Target evidence {i} " + "context " * 40 + NEEDLE for i in range(2)]
        async with graph_uow() as uow:
          pointers = [
            await storage.create_content(text.encode(), uow.storage) for text in contents
          ]
        graph = GraphForm(
          blocks=tuple(
            [
              GraphBlockForm(
                id=-(i + 1), resolver="core.text.v1", storage=storage_id, content=pointer
              )
              for i, pointer in enumerate(pointers)
            ]
            + [
              GraphBlockForm(
                id=-(i + 3),
                resolver="test.distractor.v1",
                content=f"{NEEDLE} distractor {i}",
              )
              for i in range(5)
            ]
            + [
              GraphBlockForm(
                id=-8, resolver="legacy/unloaded", content="historical evidence"
              ),
              GraphBlockForm(id=-9, resolver="core.text.v1", content="Other storage"),
              GraphBlockForm(
                id=-10,
                resolver="test.distractor.v1",
                storage=storage_id,
                content=pointers[0],
              ),
            ]
          ),
          relations=(GraphRelationForm(from_=-3, to_=-4, content="supports"),),
        )
        mapping = {row.local_id: row.id for row in (await submit_graph(graph)).blocks}
        target_ids = {mapping[-1], mapping[-2]}
        distractor_ids = {mapping[-i] for i in range(3, 8)}
        lexical = await LexicalRetrievalManager.maintain()
        semantic = await SemanticRetrievalManager.maintain()
        assert lexical.indexed == 9 and semantic.embedded == 10
        # Created after maintenance: condition queries must still find this Block.
        unindexed = (
          (
            await submit_graph(
              GraphForm(
                blocks=(
                  GraphBlockForm(
                    id=-1, resolver="core.text.v1", storage=storage_id, content=pointers[0]
                  ),
                )
              )
            )
          )
          .blocks[0]
          .id
        )
        expected_condition_ids = target_ids | {unindexed}

        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        await asyncio.wait_for(ready.wait(), timeout=10)
        sink = await SinkManager.create("core.mcp.v1", config={"pat": pat})
        assert sink.id is not None
        sink_id = sink.id
        base = f"http://127.0.0.1:{port}"
        url = f"{base}/sinks/{sink_id}/mcp"
        async with httpx.AsyncClient() as http:
          assert (
            await http.post(url, headers={"Authorization": f"Bearer {pat}"}, json={})
          ).status_code == 404
        await SinkManager.enable(sink_id, core.options.peer_id)
        async with httpx.AsyncClient() as http:
          for headers in ({}, {"Authorization": "Bearer wrong"}):
            assert (await http.post(url, headers=headers, json={})).status_code == 401
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {pat}"}) as http:
          async with Client(
            streamable_http_client(url, http_client=http), mode="legacy"
          ) as client:
            assert {tool.name for tool in (await client.list_tools()).tools} == TOOLS

            async def call(tool, **arguments):
              response = await client.call_tool(tool, arguments)
              assert not response.is_error, response
              assert response.structured_content is not None
              return response.structured_content

            catalogs = {}
            for collection in ("resolvers", "source_types", "storage_types", "storages"):
              rows, cursor = [], None
              while True:
                page = await call(
                  "meta_discover", collection=collection, limit=1, cursor=cursor
                )
                rows.extend(page["results"])
                cursor = page["next_cursor"]
                if cursor is None:
                  break
              catalogs[collection] = rows
            assert "core.text.v1" in {row["id"] for row in catalogs["resolvers"]}
            assert "legacy/unloaded" not in {row["id"] for row in catalogs["resolvers"]}
            assert catalogs["source_types"] and catalogs["storage_types"]
            discovered_storage = next(
              row["id"]
              for row in catalogs["storages"]
              if row["type"] == "postgresql_binary"
            )
            assert discovered_storage == storage_id
            storage_filter = {"storage": {"eq": discovered_storage}}
            resolver_filter = {"resolver": {"eq": "core.text.v1"}}
            and_filter = {**storage_filter, **resolver_filter}
            provider_calls = len(calls)
            for filters, expected_ids in (
              (storage_filter, expected_condition_ids | {mapping[-10]}),
              (resolver_filter, expected_condition_ids | {mapping[-9]}),
              (and_filter, expected_condition_ids),
            ):
              rows, cursor = [], None
              while True:
                page = await call("query", filters=filters, limit=1, cursor=cursor)
                assert all(
                  "score" not in row and "evidence" not in row for row in page["results"]
                )
                rows.extend(page["results"])
                cursor = page["next_cursor"]
                if cursor is None:
                  break
              assert [row["id"] for row in rows] == sorted(expected_ids)
              assert len({row["id"] for row in rows}) == len(rows)
            assert len(calls) == provider_calls
            historical = await call(
              "query", filters={"resolver": {"eq": "legacy/unloaded"}}
            )
            assert [row["id"] for row in historical["results"]] == [mapping[-8]]
            refs = [f"block:{identity}" for identity in sorted(target_ids)]
            opened = await call("open_entities", entities=refs)
            assert [row["entity"] for row in opened["results"]] == refs
            read = await call("read_blocks", blocks=sorted(target_ids), content="solved")
            assert {row["content"] for row in read["results"]} == set(contents)
            for mode in ("lexical", "semantic"):
              unfiltered = await call("query", query=NEEDLE, modes=[mode], limit=2)
              assert unfiltered["mode_errors"] == []
              assert not set(refs).intersection(
                row["entity"] for row in unfiltered["results"]
              )
              wide = await call("query", query=NEEDLE, modes=[mode], limit=6)
              higher_ranked = {row["entity"] for row in wide["results"][:5]}
              assert {f"block:{identity}" for identity in distractor_ids}.issubset(
                {row["entity"] for row in wide["results"]}
              )
              assert not set(refs).intersection(higher_ranked)
              if mode == "lexical":
                assert all(
                  int(row["entity"].split(":")[1]) in distractor_ids
                  for row in unfiltered["results"]
                )
              else:
                assert any(
                  row["entity"].startswith("relation:") for row in unfiltered["results"]
                )
              for filters in (storage_filter, resolver_filter, and_filter):
                result = await call(
                  "query", query=NEEDLE, modes=[mode], filters=filters, limit=2
                )
                assert result["mode_errors"] == []
                assert {row["entity"] for row in result["results"]} == set(refs)
                assert len(result["results"]) == 2

            # Public invalid inputs and branch boundaries, including strict ID predicates.
            for arguments in (
              {},
              {"filters": {}},
              {"filters": and_filter, "modes": []},
              {"query": NEEDLE},
              {"query": NEEDLE, "modes": []},
              {"query": NEEDLE, "modes": ["lexical"], "limit": 21},
              {"filters": {"storage": {"eq": True}}},
              {"filters": {"storage": {"eq": "-1"}}},
              {"filters": {"storage": {"eq": None}}},
              {"filters": {"storage": {"eq": 2**31}}},
              {"filters": {"resolver": {"ne": "core.text.v1"}}},
              {"filters": {"content": {"eq": "secret"}}},
            ):
              assert (await client.call_tool("query", arguments)).is_error
            for collection in ("sources", "blocks"):
              assert (
                await client.call_tool("meta_discover", {"collection": collection})
              ).is_error

        # Exact SDK request JSON, ordinary routes and non-delegating inbounds retain
        # each single filter as well as AND; null predicate serialization would fail.
        async with httpx.AsyncClient(base_url=base) as http:
          for filters in (storage_filter, resolver_filter, and_filter):
            typed = BlockQueryFilters.model_validate(filters)
            lexical_request = LexicalRetrievalRequest(query=NEEDLE, limit=2, filters=typed)
            semantic_request = SemanticRetrievalRequest(
              query=NEEDLE, options=VectorRetrievalOptions(limit=2, filters=typed)
            )
            for path in ("/retrieval/lexical", "/lexical-retrieval"):
              response = await http.post(path, json=lexical_request.model_dump(mode="json"))
              assert response.status_code == 200, response.text
              assert {
                row["block"]["id"] for row in response.json()["matches"]
              } == target_ids
            for path in ("/retrieval/semantic", "/semantic-retrieval"):
              response = await http.post(
                path, json=semantic_request.model_dump(mode="json")
              )
              assert response.status_code == 200, response.text
              assert {
                row["entity"]["id"] for row in response.json()["matches"]
              } == target_ids
        await SinkManager.disable(sink_id, core.options.peer_id)
        async with httpx.AsyncClient() as http:
          assert (
            await http.post(url, headers={"Authorization": f"Bearer {pat}"}, json={})
          ).status_code == 404
          # The ordinary DB-backed Core API remains usable with the Sink disabled.
          healthy = await http.get(f"{base}/embedding-profiles/{profile}")
          assert healthy.status_code == 200 and healthy.json()["id"] == profile
        await SinkManager.enable(sink_id, core.options.peer_id)
        # A fresh transport/client must initialize again at the exact same URL.
        async with httpx2.AsyncClient(
          headers={"Authorization": f"Bearer {pat}"}, trust_env=False
        ) as http:
          async with Client(
            streamable_http_client(url, http_client=http), mode="legacy"
          ) as reconnected:
            assert {tool.name for tool in (await reconnected.list_tools()).tools} == TOOLS

        shutdown_start = len(caplog.records)
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
        # Observe the closed endpoint and ordinary diagnostics before fallback cleanup.
        async with httpx.AsyncClient(trust_env=False, timeout=2) as http:
          with pytest.raises(httpx.ConnectError):
            await http.post(url, headers={"Authorization": f"Bearer {pat}"}, json={})
        shutdown_errors = [
          record.getMessage()
          for record in caplog.records[shutdown_start:]
          if record.levelno >= logging.ERROR
        ]
        assert not shutdown_errors, shutdown_errors
    finally:
      server.should_exit = True
      try:
        if server_task is not None:
          await asyncio.wait_for(server_task, timeout=10)
      finally:
        transport_logger.removeHandler(caplog.handler)
        listener.close()
        with core.bind():
          await SinkManager.shutdown()
        await core.aclose()
        await engine.dispose()

  asyncio.run(scenario())
