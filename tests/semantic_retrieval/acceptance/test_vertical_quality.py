"""Black-box corpus ingestion and explicit real-provider semantic quality gates."""

from __future__ import annotations

from inkcre_core.business.deployment_config import DeploymentConfigService
from inkcre_core.business.info_base.commands import submit_stars
from tests.database import read_block, read_relations


import asyncio
import base64
import copy
from contextlib import asynccontextmanager, contextmanager
import json
import os

from aiohttp import web
import pytest
import sqlmodel

from inkcre_core.business.ai import AIManager
from inkcre_core.business.info_base.resolver import register_core_resolvers
from inkcre_core.business.info_base.resolver.html import HTMLResolver
from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.organization import (
  DRAFT_GRAPH_TOOL,
  GET_DRAFT_GRAPH_SCHEMA_TOOL,
  RUMINATION_CONFIG_KEY,
  RUMINATION_CONFIG_SCHEMA,
  SUBMIT_GRAPH_TOOL,
  RuminationBehaviorResolver,
)
from inkcre_core.business.semantic_retrieval import SemanticRetrievalManager
from inkcre_core.business.job import JobManager
from inkcre_core.business.lexical_retrieval import LexicalRetrievalManager
from inkcre_core.business.source import SOURCE_COLLECT_JOB_TYPE, SourceManager
from tests.database import TestSession
from inkcre_core.schemas import AgentDefinitionModel
from tests.extensions.runtime_support import publish_extension
from inkcre_core.schemas.ai import (
  AIModelModel,
  AIProviderModel,
  AssistantMessage,
  BlockEmbeddingModel,
  ChatCapability,
  EmbeddingCapability,
  EmbeddingProfileModel,
  TextContentPart,
  ToolCall,
  ToolResultMessage,
  UserMessage,
)
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.semantic_retrieval import (
  EmbeddingMaintenanceOptions,
  VectorRetrievalOptions,
)
from inkcre_core.schemas.job import JobModel, JobStatus
from inkcre_core.schemas.lexical_retrieval import LexicalMaintenanceOptions
from inkcre_core.schemas.source import SourceModel
from extensions.memos import Extension as MemosExtension
from extensions.rss import Extension as RSSExtension
from extensions.rss.reconcile import (
  CONTENT_RELATION,
  ENCLOSURE_RELATION,
  FEED_ITEM_RESOLVER_ID,
  FEED_RELATION,
  FEED_RESOLVER_ID,
)
from extensions.rss.schema import CanonicalFeed, CanonicalFeedItem, FeedSourceConfig

from .corpus import (
  CORPUS_DIRECTORY,
  CorpusManifest,
  assert_quality_judgment,
  load_manifest,
  verify_document_digests,
)


pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("isolated_database_runtime")]

PAT = "memos_pat_" + "A" * 32
TINY_PNG = base64.b64decode(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PROVIDER_ENVIRONMENT = (
  "INKCRE_ACCEPTANCE_AI_API_KEY",
  "INKCRE_ACCEPTANCE_EMBEDDING_MODEL",
  "INKCRE_ACCEPTANCE_CHAT_MODEL",
)


class CorpusHTTPDouble:
  """Serve approved RSS/Atom/article/media/HTML inputs over real HTTP."""

  def __init__(self, manifest: CorpusManifest):
    self.manifest = manifest
    self.base_url = ""
    self._runner: web.AppRunner | None = None

  async def start(self) -> None:
    application = web.Application()
    application.router.add_get("/rss.xml", self._rss)
    application.router.add_get("/atom.xml", self._atom)
    application.router.add_get("/articles/{family}", self._article)
    application.router.add_get("/media/architecture.png", self._image)
    application.router.add_get("/sqlite-architecture.html", self._sqlite_architecture)
    self._runner = web.AppRunner(application)
    await self._runner.setup()
    site = web.TCPSite(self._runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[union-attr]
    self.base_url = f"http://127.0.0.1:{sockets[0].getsockname()[1]}"

  async def close(self) -> None:
    if self._runner is not None:
      await self._runner.cleanup()

  async def _rss(self, _request: web.Request) -> web.Response:
    data = self.manifest.producer_inputs["rss.deep-modules"]
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
      <channel>
        <title>{data["feed_title"]}</title>
        <link>{self.base_url}/rss-home</link>
        <description>{data["feed_description"]}</description>
        <atom:link rel="self" href="{self.base_url}/rss.xml" type="application/rss+xml"/>
        <item>
          <guid isPermaLink="false">acceptance:rss:deep-modules</guid>
          <title>{data["item_title"]}</title>
          <link>{self.base_url}/articles/rss</link>
          <description>{data["summary"]}</description>
          <pubDate>Fri, 07 Aug 2026 08:00:00 +0000</pubDate>
          <enclosure url="{self.base_url}/media/architecture.png"
                     type="image/png" length="{len(TINY_PNG)}"/>
        </item>
      </channel>
    </rss>"""
    return web.Response(text=body, content_type="application/rss+xml")

  async def _atom(self, _request: web.Request) -> web.Response:
    data = self.manifest.producer_inputs["atom.peer-discovery"]
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <id>acceptance:atom:peer-discovery-feed</id>
      <title>{data["feed_title"]}</title>
      <subtitle>{data["feed_description"]}</subtitle>
      <updated>2026-08-07T08:00:00Z</updated>
      <link rel="self" href="{self.base_url}/atom.xml" type="application/atom+xml"/>
      <entry>
        <id>acceptance:atom:peer-discovery</id>
        <title>{data["item_title"]}</title>
        <summary>{data["summary"]}</summary>
        <updated>2026-08-07T08:00:00Z</updated>
        <link rel="alternate" href="{self.base_url}/articles/atom"/>
      </entry>
    </feed>"""
    return web.Response(text=body, content_type="application/atom+xml")

  async def _article(self, request: web.Request) -> web.Response:
    family = request.match_info["family"]
    alias = "rss.deep-modules" if family == "rss" else "atom.peer-discovery"
    data = self.manifest.producer_inputs[alias]
    body = f"""<!doctype html><html><head><title>{data["item_title"]}</title></head>
    <body><nav>Acceptance navigation</nav><main><article>
      <h1>{data["item_title"]}</h1><p>{data["article"]}</p>
    </article></main></body></html>"""
    return web.Response(text=body, content_type="text/html")

  async def _image(self, _request: web.Request) -> web.Response:
    return web.Response(body=TINY_PNG, content_type="image/png")

  async def _sqlite_architecture(self, _request: web.Request) -> web.Response:
    document = self.manifest.documents["sqlite.architecture-source"]
    return web.Response(
      body=(CORPUS_DIRECTORY / document.artifact).read_bytes(),
      content_type="text/html",
    )


def _required_id(value: int | None) -> int:
  if value is None:
    raise AssertionError("persisted acceptance entity has no ID")
  return value


def _resource_id(name: str) -> int:
  return int(name.rpartition("/")[2])


@contextmanager
def _memos_client():
  publication = publish_extension(
    MemosExtension, {"personal_access_token": PAT}, persist_config=True
  )
  try:
    with publication.client as client:
      yield client
  finally:
    publication.unpublish()


def _ingest_memo(manifest: CorpusManifest) -> int:
  data = manifest.producer_inputs["memo.design-capture"]
  headers = {"Authorization": f"Bearer {PAT}"}
  with _memos_client() as client:
    attachment = client.post(
      "/memos/api/v1/attachments",
      headers=headers,
      json={
        "filename": "architecture-context.png",
        "type": "image/png",
        "content": base64.b64encode(TINY_PNG).decode(),
      },
    )
    assert attachment.status_code == 200, attachment.text
    attachment_name = attachment.json()["name"]
    memo = client.post(
      "/memos/api/v1/memos",
      headers=headers,
      json={
        "content": data["body"],
        "visibility": "PRIVATE",
        "createTime": "2026-08-07T08:00:00Z",
        "attachments": [{"name": attachment_name}],
      },
    )
    assert memo.status_code == 200, memo.text
    memo_id = _resource_id(memo.json()["name"])
    comment = client.post(
      f"/memos/api/v1/memos/{memo_id}/comments",
      headers=headers,
      json={
        "content": data["comment"],
        "visibility": "PRIVATE",
        "createTime": "2026-08-07T08:01:00Z",
        "attachments": [],
      },
    )
    assert comment.status_code == 200, comment.text
    return memo_id


def _ingest_text_memo(content: str) -> int:
  with _memos_client() as client:
    response = client.post(
      "/memos/api/v1/memos",
      headers={"Authorization": f"Bearer {PAT}"},
      json={
        "content": content,
        "visibility": "PRIVATE",
        "createTime": "2026-08-07T08:02:00Z",
        "attachments": [],
      },
    )
  assert response.status_code == 200, response.text
  return _resource_id(response.json()["name"])


def _create_source(source_type: str, config: FeedSourceConfig) -> int:
  with TestSession() as db:
    source = SourceModel(
      type=source_type,
      nickname="semantic-retrieval-acceptance",
      config=config.model_dump(mode="json"),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return _required_id(source.id)


async def _collect_source(source_id: int) -> None:
  job = await JobManager.create(
    SOURCE_COLLECT_JOB_TYPE,
    {"source": source_id, "config": {}},
  )
  job_id = _required_id(job.id)
  assert await JobManager.run(job_id)
  with TestSession() as db:
    closed = db.get(JobModel, job_id)
  assert closed is not None
  assert closed.status is JobStatus.FINISHED, closed.state


def _feed_item_id(source_id: int, item_title: str) -> int:
  with TestSession() as db:
    feed = next(
      block
      for block in db.exec(
        sqlmodel.select(BlockModel).where(BlockModel.resolver == FEED_RESOLVER_ID)
      ).all()
      if CanonicalFeed.model_validate_json(block.content).source_instance_id == source_id
    )
    feed_id = _required_id(feed.id)
    item_ids = [
      relation.from_
      for relation in db.exec(
        sqlmodel.select(RelationModel).where(
          RelationModel.to_ == feed_id,
          RelationModel.content == FEED_RELATION,
        )
      ).all()
    ]
    item = next(
      db.get_one(BlockModel, item_id)
      for item_id in item_ids
      if CanonicalFeedItem.model_validate_json(
        db.get_one(BlockModel, item_id).content
      ).title
      == item_title
    )
    return _required_id(item.id)


@asynccontextmanager
async def _ingested_corpus(manifest: CorpusManifest):
  register_core_resolvers()
  await StorageManager.setup_builtin_storages_async()
  MemosExtension._init_resolvers()
  RSSExtension._init_resolvers()
  RSSExtension._init_sources()
  await SourceManager.sync_source_types_async()
  await JobManager.sync_job_types()

  server = CorpusHTTPDouble(manifest)
  aliases: dict[str, int] = {}
  await server.start()
  try:
    aliases["memo.design-capture"] = await asyncio.to_thread(_ingest_memo, manifest)

    source_types = {
      "rss": "extensions.rss.rss.Source",
      "atom": "extensions.rss.atom.Source",
    }
    for family, alias in (
      ("rss", "rss.deep-modules"),
      ("atom", "atom.peer-discovery"),
    ):
      source_id = _create_source(
        source_types[family],
        FeedSourceConfig(
          feed_url=f"{server.base_url}/{family}.xml",
          fetch_full_text=True,
          download_enclosures=family == "rss",
          target_storage_id=-4,
          user_agent="InKCre semantic-retrieval acceptance/1",
        ),
      )
      await _collect_source(source_id)
      aliases[alias] = _feed_item_id(
        source_id,
        manifest.producer_inputs[alias]["item_title"],
      )

    sqlite_url = f"{server.base_url}/sqlite-architecture.html"
    root = await submit_stars(HTMLResolver.create_graph(sqlite_url))
    aliases["sqlite.architecture-source"] = _required_id(root.id)
    yield aliases
  finally:
    await server.close()


def _assert_ingested_graph(aliases: dict[str, int]) -> None:
  with TestSession() as db:
    memo = db.get_one(BlockModel, aliases["memo.design-capture"])
    assert memo.resolver == "extensions.memos.memo.v1"
    rss_item = db.get_one(BlockModel, aliases["rss.deep-modules"])
    atom_item = db.get_one(BlockModel, aliases["atom.peer-discovery"])
    assert rss_item.resolver == atom_item.resolver == FEED_ITEM_RESOLVER_ID
    enclosure = db.exec(
      sqlmodel.select(RelationModel).where(
        RelationModel.from_ == _required_id(rss_item.id),
        RelationModel.content == ENCLOSURE_RELATION,
      )
    ).one()
    materialized = db.exec(
      sqlmodel.select(RelationModel).where(
        RelationModel.from_ == enclosure.to_,
        RelationModel.content == CONTENT_RELATION,
      )
    ).one()
    image = db.get_one(BlockModel, materialized.to_)
    assert image.resolver == "core.image.v1"
    sqlite_root = db.get_one(BlockModel, aliases["sqlite.architecture-source"])
    assert (sqlite_root.resolver, sqlite_root.storage) == ("core.html.v1", -1)


@pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)
def test_real_producer_corpus_supports_exact_and_chinese_lexical_recall(
  async_runner,
):
  manifest = load_manifest()
  verify_document_digests(manifest)

  async def journey() -> None:
    async with _ingested_corpus(manifest) as aliases:
      _assert_ingested_graph(aliases)
      target = await asyncio.to_thread(
        _ingest_text_memo,
        "部署演练记录了星间链路故障注入的触发顺序。",
      )
      distractor = await asyncio.to_thread(
        _ingest_text_memo,
        "部署演练记录了卫星通信异常的排查过程。",
      )

      report = await LexicalRetrievalManager.maintain(
        LexicalMaintenanceOptions(max_records=500)
      )
      assert report.failed == 0

      technical = await LexicalRetrievalManager.retrieve_local("sqlite3_prepare_v2")
      assert technical.matches[0].block.id == aliases["sqlite.architecture-source"]
      assert technical.matches[0].evidence in {"label_substring", "text_substring"}

      chinese = await LexicalRetrievalManager.retrieve_local("链路故障注入")
      assert chinese.matches[0].block.id == target
      assert distractor not in {match.block.id for match in chinese.matches}

  async_runner.run(journey())


def _provider_environment_available() -> bool:
  return bool(os.getenv("INKCRE_TEST_DATABASE_URL")) and all(
    os.getenv(name) for name in PROVIDER_ENVIRONMENT
  )


def _create_ai_facts(
  *,
  api_key: str,
  base_url: str | None,
  embedding_model_name: str,
  chat_model_name: str,
  dimensions: int,
) -> tuple[int, int]:
  config: dict[str, str] = {"api_key": api_key}
  if base_url:
    config["base_url"] = base_url
  with TestSession() as db:
    provider = AIProviderModel(
      name="Semantic retrieval acceptance provider",
      dialect="core.openai-compatible.v1",
      config=config,
    )
    db.add(provider)
    db.flush()
    provider_id = _required_id(provider.id)
    embedding_model = AIModelModel(
      provider=provider_id,
      native_model_id=embedding_model_name,
      capabilities=(
        EmbeddingCapability(input_modalities=["text"], output_modalities=["vector"]),
      ),
    )
    chat_model = AIModelModel(
      provider=provider_id,
      native_model_id=chat_model_name,
      capabilities=(
        ChatCapability(
          input_modalities=["text"],
          output_modalities=["text"],
          features=["tool_calling"],
        ),
      ),
    )
    db.add_all((embedding_model, chat_model))
    db.flush()
    chat_model_id = _required_id(chat_model.id)
    profile = EmbeddingProfileModel(
      name="Semantic retrieval acceptance profile",
      ai_model=_required_id(embedding_model.id),
      dimensions=dimensions,
    )
    db.add(profile)
    db.flush()
    profile_id = _required_id(profile.id)
    agent = AgentDefinitionModel(
      name="SQLite architecture rumination acceptance Agent",
      system_prompt=(
        "Ruminate the focal SQLite Architecture document into one focused semantic block. "
        "First request the core.text.v1 draft schema. Then draft exactly one concise block "
        "beginning with 'SQLite pager:' that explains the pager's page cache, locking, "
        "rollback, commit, and transaction responsibilities. Add an 'interpretation' "
        "relation from the positive focal block ID in the user context to the drafted "
        "negative block ID, submit that graph exactly once, and finish after success."
      ),
      tools=(GET_DRAFT_GRAPH_SCHEMA_TOOL, DRAFT_GRAPH_TOOL, SUBMIT_GRAPH_TOOL),
      tool_choice="auto",
      model=chat_model_id,
      max_model_calls_per_turn=5,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return profile_id, _required_id(agent.id)


async def _exercise_quality(
  manifest: CorpusManifest,
  aliases: dict[str, int],
  *,
  profile_id: int,
  agent_id: int,
) -> None:
  await DeploymentConfigService.replace(
    RUMINATION_CONFIG_KEY, RUMINATION_CONFIG_SCHEMA, {"agent": agent_id}
  )

  sqlite_id = aliases["sqlite.architecture-source"]
  previous_relations = {
    relation.id
    for relation in read_relations(
      sqlite_id,
      include_in=False,
      include_out=True,
    )
  }
  await RuminationBehaviorResolver.ruminate_local(sqlite_id)
  interpretation = max(
    (
      relation
      for relation in read_relations(
        sqlite_id,
        include_in=False,
        include_out=True,
        content="interpretation",
      )
      if relation.id not in previous_relations
    ),
    key=lambda relation: _required_id(relation.id),
  )
  pager = read_block(interpretation.to_)
  assert pager is not None
  assert "pager" in pager.content.casefold()
  aliases["sqlite.pager"] = _required_id(pager.id)

  report = await SemanticRetrievalManager.maintain(
    profile_id,
    EmbeddingMaintenanceOptions(
      max_embeddings=10_000,
      batch_size=10,
      scan_page_size=100,
    ),
  )
  assert report.failed == 0, report.diagnostics
  _assert_judged_blocks_are_fresh(manifest, aliases, profile_id)
  for judgment in manifest.quality_queries:
    result = await SemanticRetrievalManager.retrieve(
      judgment.query,
      profile_id,
      VectorRetrievalOptions(limit=20),
    )
    ranked: list[tuple[str, int]] = []
    for match in result.matches:
      ranked.append((match.type, _required_id(match.entity.id)))
    assert_quality_judgment(judgment, aliases, tuple(ranked))


def _assert_judged_blocks_are_fresh(
  manifest: CorpusManifest,
  aliases: dict[str, int],
  profile_id: int,
) -> None:
  judged_aliases = {
    alias
    for judgment in manifest.quality_queries
    for alias in judgment.primary + judgment.distractors + judgment.must_outrank
  }
  with TestSession() as db:
    profile = db.get(EmbeddingProfileModel, profile_id)
    assert profile is not None
    for alias in judged_aliases:
      block_id = aliases[alias]
      block = db.get(BlockModel, block_id)
      record = db.get(BlockEmbeddingModel, (profile_id, block_id))
      assert block is not None
      assert record is not None, f"judged alias {alias!r} has no embedding record"
      assert len(record.embedding) == profile.dimensions
      assert record.updated_at >= profile.updated_at
      assert record.updated_at >= block.updated_at


def _deterministic_vector(text: str) -> tuple[float, float, float, float, float]:
  normalized = text.casefold()
  if "sqlite pager:" in normalized or "which sqlite subsystem" in normalized:
    return (0.0, 0.0, 0.0, 1.0, 0.0)
  if "architecture of sqlite" in normalized:
    return (0.0, 0.0, 0.0, 0.7, 0.7)
  if "fleeting architecture decision" in normalized:
    return (1.0, 0.0, 0.0, 0.0, 0.0)
  if "deep module" in normalized or "small stable interface" in normalized:
    return (0.0, 1.0, 0.0, 0.0, 0.0)
  if "capability discovery" in normalized or "live peers" in normalized:
    return (0.0, 0.0, 1.0, 0.0, 0.0)
  return (0.0, 0.0, 0.0, 0.0, 1.0)


@pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)
def test_vertical_quality_control_flow_with_deterministic_ai(async_runner, monkeypatch):
  manifest = load_manifest()

  async def embed(_cls, _model, inputs, dimensions):
    assert dimensions == 5
    return tuple(_deterministic_vector(text) for text in inputs)

  model_calls = 0

  async def chat(_cls, _model, messages, tools, tool_choice):
    nonlocal model_calls
    model_calls += 1
    assert tool_choice == "auto"
    assert {tool.id for tool in tools} == {
      GET_DRAFT_GRAPH_SCHEMA_TOOL,
      DRAFT_GRAPH_TOOL,
      SUBMIT_GRAPH_TOOL,
    }
    if model_calls == 1:
      return AssistantMessage(
        tool_calls=(
          ToolCall(
            id="schema",
            tool=GET_DRAFT_GRAPH_SCHEMA_TOOL,
            arguments={"resolver_types": ["core.text.v1"]},
          ),
        )
      )
    last = messages[-1]
    assert isinstance(last, ToolResultMessage)
    if last.results[0].tool_call_id == "schema":
      return AssistantMessage(
        tool_calls=(
          ToolCall(
            id="draft",
            tool=DRAFT_GRAPH_TOOL,
            arguments={
              "resolver_type": "core.text.v1",
              "input": {
                "text": (
                  "SQLite pager: owns the page cache and coordinates database-file "
                  "locking, rollback, commit, and transaction behavior."
                )
              },
              "local_block_id_start": -1,
            },
          ),
        )
      )
    if last.results[0].tool_call_id == "draft":
      graph = copy.deepcopy(last.results[0].content)
      assert isinstance(graph, dict)
      user = next(message for message in messages if isinstance(message, UserMessage))
      context = user.content[0]
      assert isinstance(context, TextContentPart)
      focal_id = json.loads(context.text)["focal_block"]["id"]
      relations = graph["relations"]
      assert isinstance(relations, list)
      relations.append(
        {
          "content": "interpretation",
          "from_": focal_id,
          "to_": -1,
        }
      )
      return AssistantMessage(
        tool_calls=(
          ToolCall(
            id="submit",
            tool=SUBMIT_GRAPH_TOOL,
            arguments={"graph": graph},
          ),
        )
      )
    assert last.results[0].tool_call_id == "submit"
    return AssistantMessage(content="complete")

  monkeypatch.setattr(AIManager, "embed", classmethod(embed))
  monkeypatch.setattr(AIManager, "chat", classmethod(chat))

  async def journey() -> None:
    async with _ingested_corpus(manifest) as aliases:
      await AIManager.sync_dialects_async()
      profile_id, agent_id = _create_ai_facts(
        api_key="deterministic-test-key",
        base_url="https://provider.invalid/v1",
        embedding_model_name="deterministic-embedding",
        chat_model_name="deterministic-chat",
        dimensions=5,
      )
      await _exercise_quality(
        manifest,
        aliases,
        profile_id=profile_id,
        agent_id=agent_id,
      )
      assert model_calls == 4

  async_runner.run(journey())


@pytest.mark.acceptance
@pytest.mark.skipif(
  not _provider_environment_available(),
  reason=(
    "set INKCRE_TEST_DATABASE_URL and the INKCRE_ACCEPTANCE_AI_* model/key values "
    "to run real-provider semantic quality"
  ),
)
def test_real_provider_quality_and_rumination_gain(
  async_runner,
):
  manifest = load_manifest()

  async def journey() -> None:
    async with _ingested_corpus(manifest) as aliases:
      await AIManager.sync_dialects_async()
      profile_id, agent_id = _create_ai_facts(
        api_key=os.environ["INKCRE_ACCEPTANCE_AI_API_KEY"],
        base_url=os.getenv("INKCRE_ACCEPTANCE_AI_BASE_URL"),
        embedding_model_name=os.environ["INKCRE_ACCEPTANCE_EMBEDDING_MODEL"],
        chat_model_name=os.environ["INKCRE_ACCEPTANCE_CHAT_MODEL"],
        dimensions=int(os.getenv("INKCRE_ACCEPTANCE_EMBEDDING_DIMENSIONS", "256")),
      )
      await _exercise_quality(
        manifest,
        aliases,
        profile_id=profile_id,
        agent_id=agent_id,
      )

  async_runner.run(journey())
