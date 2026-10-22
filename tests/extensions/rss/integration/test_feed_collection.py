"""Real RSS/Atom HTTP doubles collected through PostgreSQL source jobs."""

from __future__ import annotations

from tests.database import read_block, read_relations


import asyncio
import os
from pathlib import Path
from unittest import mock

from aiohttp import web
import fastapi
import httpx
import pytest
import pydantic
import sqlmodel

from app.business.info_base.resolver import ResolverManager, register_core_resolvers
from app.business.info_base.storage import StorageManager
from app.business.info_base.storage.postgresql import PostgreSQLBlobPointer
from app.business.job import JobManager
from app.business.source import SOURCE_COLLECT_JOB_TYPE, SourceManager
from tests.database import TestSession
from app.schemas.info_base.block import BlockModel
from app.schemas.info_base.relation import RelationModel
from app.schemas.info_base.storage import StorageBlobModel
from app.schemas.job import JobModel, JobStatus
from app.schemas.source import SourceModel
from extensions.rss import Extension
from extensions.rss.api import register_api
from extensions.rss.reconcile import (
  CONTENT_RELATION,
  ENCLOSURE_RELATION,
  FEED_RELATION,
  FEED_RESOLVER_ID,
  FULL_TEXT_RELATION,
)
from extensions.rss.reconcile import FeedGraphReconciler
from extensions.rss.schema import (
  CanonicalEnclosure,
  CanonicalFeed,
  CanonicalFeedItem,
  FeedSourceConfig,
)


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)

ASSETS = Path(__file__).parents[3] / "assets" / "semantic-content"


@pytest.fixture(scope="module", autouse=True)
def _generated_semantic_content_assets(semantic_content_assets: Path) -> None:
  assert semantic_content_assets == ASSETS


class FeedHTTPDouble:
  def __init__(self):
    self.base_url = ""
    self.rss_revision = 1
    self.rss_etag = '"rss-v1"'
    self.omit_first_rss_item = False
    self.atom_returns_wrong_family = True
    self.feed_requests = 0
    self.article_requests = 0
    self.enclosure_requests = 0
    self._runner: web.AppRunner | None = None

  async def start(self) -> None:
    app = web.Application()
    app.router.add_get("/rss.xml", self._rss)
    app.router.add_get("/rss-alt.xml", self._rss)
    app.router.add_get("/rss-no-self.xml", self._rss)
    app.router.add_get("/atom.xml", self._atom)
    app.router.add_get("/articles/{name}", self._article)
    app.router.add_get("/media/{name}", self._enclosure)
    self._runner = web.AppRunner(app)
    await self._runner.setup()
    site = web.TCPSite(self._runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[union-attr]
    port = sockets[0].getsockname()[1]
    self.base_url = f"http://127.0.0.1:{port}"

  async def close(self) -> None:
    if self._runner is not None:
      await self._runner.cleanup()

  def _rss_body(self, *, include_self: bool = True) -> str:
    updated_title = "First article updated" if self.rss_revision > 1 else "First article"
    first_item = (
      ""
      if self.omit_first_rss_item
      else f"""
        <item>
          <guid isPermaLink="false">rss-guid-1</guid>
          <title>{updated_title}</title>
          <link>/articles/first</link>
          <description><![CDATA[<p>Feed-authored summary.</p>]]></description>
          <pubDate>Sat, 01 Aug 2026 08:00:00 +0000</pubDate>
          <enclosure url="/media/image.png" type="image/png" length="1234"/>
          <enclosure url="/media/audio.wav" type="audio/wav" length="1234"/>
          <enclosure url="/media/video.mp4" type="video/mp4" length="1234"/>
          <enclosure url="/media/document.pdf" type="application/pdf" length="1234"/>
          <enclosure url="/media/book.epub" type="application/epub+zip" length="1234"/>
          <enclosure url="/media/archive.zip" type="application/zip" length="1234"/>
          <enclosure url="/media/unknown.bin"
                     type="application/x-inkcre-unknown" length="1234"/>
        </item>"""
    )
    new_item = (
      f"""
      <item>
        <guid isPermaLink="false">rss-guid-2</guid>
        <title>Second article</title>
        <link>{self.base_url}/articles/second</link>
        <description>Second feed summary.</description>
        <pubDate>Sun, 02 Aug 2026 08:00:00 +0000</pubDate>
      </item>"""
      if self.rss_revision > 1
      else ""
    )
    self_link = (
      f'<atom:link rel="self" href="{self.base_url}/rss.xml" type="application/rss+xml"/>'
      if include_self
      else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
      <channel>
        <title>RSS Integration Feed</title>
        <link>{self.base_url}/home</link>
        {self_link}
        <description>Protocol-authored feed description.</description>
        {first_item}
        <item>
          <title>Unidentified historical item</title>
          <description>
            Its authored time is used only as a later admission cutoff.
          </description>
          <pubDate>Mon, 15 Jan 2024 12:00:00 +0000</pubDate>
        </item>
        <item></item>
        {new_item}
      </channel>
    </rss>"""

  def _atom_body(self) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <id>tag:example.test,2026:feed</id>
      <title>Atom Integration Feed</title>
      <updated>2026-08-02T08:00:00Z</updated>
      <link rel="self" href="{self.base_url}/atom.xml" type="application/atom+xml"/>
      <link rel="alternate" href="{self.base_url}/atom-home"/>
      <entry>
        <id>tag:example.test,2026:item-1</id>
        <title>Atom item</title>
        <updated>2026-08-02T08:00:00Z</updated>
        <link rel="alternate" href="/articles/atom"/>
        <link rel="enclosure" href="/media/image.png"
              type="application/pdf" length="1234" title="Atom image"/>
        <summary type="html">&lt;p&gt;Atom feed summary.&lt;/p&gt;</summary>
      </entry>
    </feed>"""

  async def _rss(self, request: web.Request) -> web.Response:
    self.feed_requests += 1
    if request.headers.get("If-None-Match") == self.rss_etag:
      return web.Response(status=304, headers={"ETag": self.rss_etag})
    return web.Response(
      text=self._rss_body(include_self=request.path != "/rss-no-self.xml"),
      content_type="application/rss+xml",
      headers={"ETag": self.rss_etag, "Last-Modified": "Sun, 02 Aug 2026 08:00:00 GMT"},
    )

  async def _atom(self, _request: web.Request) -> web.Response:
    self.feed_requests += 1
    body = self._rss_body() if self.atom_returns_wrong_family else self._atom_body()
    return web.Response(body=body.encode(), content_type="application/xml")

  async def _article(self, request: web.Request) -> web.Response:
    self.article_requests += 1
    name = request.match_info["name"]
    return web.Response(
      text=f"""<!doctype html><html><body><nav>Navigation</nav><article>
      <h1>{name.title()} full article</h1>
      <p>This is the independently fetched main text for {name}.</p>
      </article></body></html>""",
      content_type="text/html",
    )

  async def _enclosure(self, request: web.Request) -> web.Response:
    self.enclosure_requests += 1
    name = request.match_info["name"]
    content_type = (
      "image/png" if name in {"document.pdf", "image.png"} else "application/octet-stream"
    )
    return web.Response(
      body=(ASSETS / name).read_bytes(),
      content_type=content_type,
    )


def _create_source(source_type: str, config: FeedSourceConfig) -> int:
  with TestSession() as db_session:
    source = SourceModel(
      type=source_type,
      nickname="rss-integration-test",
      config=config.model_dump(mode="json"),
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)
    assert source.id is not None
    return source.id


def _reload_job(job_id: int) -> JobModel:
  with TestSession() as db_session:
    return db_session.exec(sqlmodel.select(JobModel).where(JobModel.id == job_id)).one()


async def _run_job(source_id: int, config: dict | None = None) -> JobModel:
  job = await JobManager.create(
    SOURCE_COLLECT_JOB_TYPE,
    {"source": source_id, "config": config or {}},
  )
  assert job.id is not None
  assert await JobManager.run(job.id)
  return _reload_job(job.id)


def _feed_roots(source_id: int) -> list[tuple[BlockModel, CanonicalFeed]]:
  with TestSession() as db_session:
    matches = []
    for block in db_session.exec(
      sqlmodel.select(BlockModel).where(BlockModel.resolver == FEED_RESOLVER_ID)
    ).all():
      canonical = CanonicalFeed.model_validate_json(block.content)
      if canonical.source_instance_id == source_id:
        matches.append((block, canonical))
  return matches


def _feed_root(source_id: int) -> tuple[BlockModel, CanonicalFeed]:
  matches = _feed_roots(source_id)
  assert len(matches) == 1
  return matches[0]


def _feed_items(feed_block_id: int) -> list[tuple[BlockModel, CanonicalFeedItem]]:
  with TestSession() as db_session:
    relations = db_session.exec(
      sqlmodel.select(RelationModel).where(
        RelationModel.to_ == feed_block_id,
        RelationModel.content == FEED_RELATION,
      )
    ).all()
    return [
      (
        db_session.get_one(BlockModel, relation.from_),
        CanonicalFeedItem.model_validate_json(
          db_session.get_one(BlockModel, relation.from_).content
        ),
      )
      for relation in relations
    ]


def _graph_block_ids(source_id: int) -> tuple[set[int], set[object]]:
  feed_roots = _feed_roots(source_id)
  if not feed_roots:
    return set(), set()
  block_ids = {block.id for block, _ in feed_roots if block.id is not None}
  blob_ids: set[object] = set()
  with TestSession() as db_session:
    frontier = list(block_ids)
    while frontier:
      current = frontier.pop()
      relations = read_relations(current, db_session=db_session)
      for relation in relations:
        other = relation.to_ if relation.from_ == current else relation.from_
        if other not in block_ids:
          block_ids.add(other)
          frontier.append(other)
    for block_id in block_ids:
      block = db_session.get(BlockModel, block_id)
      if block is not None and block.storage == -4:
        blob_ids.add(PostgreSQLBlobPointer.model_validate_json(block.content).blob_id)
  return block_ids, blob_ids


def _cleanup(source_ids: set[int]) -> None:
  all_block_ids: set[int] = set()
  all_blob_ids: set[object] = set()
  for source_id in source_ids:
    block_ids, blob_ids = _graph_block_ids(source_id)
    all_block_ids.update(block_ids)
    all_blob_ids.update(blob_ids)
  with TestSession() as db_session:
    if all_block_ids:
      for block in db_session.exec(
        sqlmodel.select(BlockModel).where(
          BlockModel.id.in_(all_block_ids)  # pyrefly: ignore[missing-attribute]
        )
      ).all():
        db_session.delete(block)
    for source_id in source_ids:
      source = db_session.get(SourceModel, source_id)
      if source is not None:
        db_session.delete(source)
    if all_blob_ids:
      for blob in db_session.exec(
        sqlmodel.select(StorageBlobModel).where(
          StorageBlobModel.id.in_(all_blob_ids)  # pyrefly: ignore[missing-attribute]
        )
      ).all():
        db_session.delete(blob)
    db_session.commit()
  for source_id in source_ids:
    SourceManager.SOURCES.pop(source_id, None)


async def _exercise_rss() -> None:
  server = FeedHTTPDouble()
  await server.start()
  source_ids: set[int] = set()
  try:
    source_id = _create_source(
      "extensions.rss.rss.Source",
      FeedSourceConfig(feed_url=f"{server.base_url}/rss.xml"),
    )
    source_ids.add(source_id)

    first_job = await JobManager.create(
      SOURCE_COLLECT_JOB_TYPE,
      {"source": source_id, "config": {}},
    )
    assert first_job.id is not None
    claims = await asyncio.gather(
      JobManager.run(first_job.id),
      JobManager.run(first_job.id),
    )
    assert sorted(claims) == [False, True]
    first_job = _reload_job(first_job.id)
    assert first_job.status == JobStatus.FINISHED
    assert first_job.state["items"] == {
      "created": 2,
      "updated": 0,
      "unchanged": 0,
      "skipped": 0,
    }
    assert any(item["code"] == "malformed_item" for item in first_job.state["diagnostics"])
    assert server.feed_requests == 1

    feed_block, feed = _feed_root(source_id)
    assert feed.family == "rss"
    assert feed.declared_self_url == f"{server.base_url}/rss.xml"
    assert feed_block.id is not None
    items = _feed_items(feed_block.id)
    assert len(items) == 2
    exact_item = next(item for item in items if item[1].source_native_id == "rss-guid-1")
    exact_item_id = exact_item[0].id
    assert exact_item_id is not None
    full_text = read_relations(
      exact_item_id,
      include_in=False,
      include_out=True,
      content=FULL_TEXT_RELATION,
    )
    assert len(full_text) == 1
    assert "independently fetched main text" in (
      await ResolverManager.get(exact_item[0]).get_text(materialize_missing=False) or ""
    )

    not_modified_job = await _run_job(source_id)
    assert not_modified_job.status == JobStatus.FINISHED
    assert not_modified_job.state["not_modified"] is True
    assert len(_feed_items(feed_block.id)) == 2

    server.rss_revision = 2
    server.rss_etag = '"rss-v2"'
    update_job = await _run_job(source_id)
    assert update_job.status == JobStatus.FINISHED
    assert update_job.state["items"] == {
      "created": 1,
      "updated": 1,
      "unchanged": 0,
      "skipped": 1,
    }
    assert len(_feed_items(feed_block.id)) == 3
    updated_item = next(
      item
      for item in _feed_items(feed_block.id)
      if item[1].source_native_id == "rss-guid-1"
    )
    assert updated_item[0].id == exact_item_id
    assert updated_item[1].title == "First article updated"

    enclosure_relations = read_relations(
      exact_item_id,
      include_in=False,
      include_out=True,
      content=ENCLOSURE_RELATION,
    )
    assert len(enclosure_relations) == 7
    enclosure_ids: dict[str, int] = {}
    for relation in enclosure_relations:
      enclosure_block = read_block(relation.to_)
      assert enclosure_block is not None
      enclosure = CanonicalEnclosure.model_validate_json(enclosure_block.content)
      enclosure_ids[enclosure.url.rsplit("/", 1)[-1]] = relation.to_
    expected_resolvers = {
      "image.png": "core.image.v1",
      "audio.wav": "core.audio.v1",
      "video.mp4": "core.video.v1",
      "document.pdf": "core.pdf.v1",
      "book.epub": "core.epub.v1",
      "archive.zip": "core.zip.v1",
      "unknown.bin": "core.file.v1",
    }
    api = fastapi.FastAPI()
    router = fastapi.APIRouter(prefix="/rss")
    register_api(router)
    api.include_router(router)
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app=api),
      base_url="http://test",
    ) as client:
      concurrent = await asyncio.gather(
        client.post(
          "/rss/enclosures/materialize",
          json={"enclosure_block_ids": [enclosure_ids["image.png"]]},
        ),
        client.post(
          "/rss/enclosures/materialize",
          json={"enclosure_block_ids": [enclosure_ids["image.png"]]},
        ),
      )
      assert sorted(item.json()["results"][0]["status"] for item in concurrent) == [
        "created",
        "existing",
      ]
      ordered_names = tuple(expected_resolvers)
      response = await client.post(
        "/rss/enclosures/materialize",
        json={
          "enclosure_block_ids": [
            *(enclosure_ids[name] for name in ordered_names),
            2_147_000_000,
          ]
        },
      )
    assert response.status_code == 200
    results = response.json()["results"]
    assert [result["resolver_id"] for result in results[:-1]] == [
      expected_resolvers[name] for name in ordered_names
    ]
    assert results[-1]["status"] == "failed"
    for name in ordered_names:
      content_relations = read_relations(
        enclosure_ids[name],
        include_in=False,
        include_out=True,
        content=CONTENT_RELATION,
      )
      assert len(content_relations) == 1
      content_block = read_block(content_relations[0].to_)
      assert content_block is not None
      assert await content_block.get_hydrated_content() == (ASSETS / name).read_bytes()
      assert await ResolverManager.get(content_block).get_solved_content() is not None

    server.rss_etag = '"rss-v3"'
    replay_job = await _run_job(source_id)
    assert replay_job.state["items"] == {
      "created": 0,
      "updated": 0,
      "unchanged": 2,
      "skipped": 1,
    }

    server.omit_first_rss_item = True
    server.rss_etag = '"rss-v4"'
    missing_old_job = await _run_job(source_id)
    assert missing_old_job.status == JobStatus.FINISHED
    assert len(_feed_items(feed_block.id)) == 3

    alternate_feed_url = f"{server.base_url}/rss-alt.xml"
    with TestSession() as db_session:
      source = db_session.get_one(SourceModel, source_id)
      source.config = {**source.config, "feed_url": alternate_feed_url}
      db_session.add(source)
      db_session.commit()
    changed_url_job = await _run_job(source_id)
    assert changed_url_job.status == JobStatus.FINISHED
    assert changed_url_job.state.get("not_modified") is not True
    assert len(_feed_items(feed_block.id)) == 3
    with TestSession() as db_session:
      state = db_session.get_one(SourceModel, source_id).state
      assert state["snapshot_configured_url"] == alternate_feed_url
      assert state["snapshot_feed_block_id"] == feed_block.id

    before_scheduled = server.feed_requests
    await _run_job(source_id)
    assert server.feed_requests == before_scheduled + 1

    no_self_feed_url = f"{server.base_url}/rss-no-self.xml"
    with TestSession() as db_session:
      source = db_session.get_one(SourceModel, source_id)
      source.config = {**source.config, "feed_url": no_self_feed_url}
      db_session.add(source)
      db_session.commit()
    new_feed_job = await _run_job(source_id)
    assert new_feed_job.status == JobStatus.FINISHED
    roots_after_identity_change = _feed_roots(source_id)
    assert len(roots_after_identity_change) == 2
    with TestSession() as db_session:
      new_state = db_session.get_one(SourceModel, source_id).state
      assert new_state["snapshot_feed_block_id"] != feed_block.id

    discard_source_id = _create_source(
      "extensions.rss.rss.Source",
      FeedSourceConfig(
        feed_url=f"{server.base_url}/rss.xml",
        fetch_full_text=False,
        unidentified_item_behavior="discard",
      ),
    )
    source_ids.add(discard_source_id)
    discard_job = await _run_job(discard_source_id)
    assert discard_job.state["items"] == {
      "created": 1,
      "updated": 0,
      "unchanged": 0,
      "skipped": 1,
    }

    server.omit_first_rss_item = False
    retry_source_id = _create_source(
      "extensions.rss.rss.Source",
      FeedSourceConfig(
        feed_url=f"{server.base_url}/rss.xml",
        fetch_full_text=False,
        unidentified_item_behavior="discard",
      ),
    )
    source_ids.add(retry_source_id)
    original_reconcile_item = FeedGraphReconciler.reconcile_item
    reconcile_calls = 0

    async def fail_second_primary(*args, **kwargs):
      nonlocal reconcile_calls
      reconcile_calls += 1
      if reconcile_calls == 2:
        raise RuntimeError("injected second-primary failure")
      return await original_reconcile_item(*args, **kwargs)

    with mock.patch.object(
      FeedGraphReconciler,
      "reconcile_item",
      side_effect=fail_second_primary,
    ):
      failed_job = await _run_job(retry_source_id)
    assert failed_job.status == JobStatus.FAILED
    assert any(
      diagnostic["code"] == "primary_persistence_failed"
      for diagnostic in failed_job.state["diagnostics"]
    )
    with TestSession() as db_session:
      assert db_session.get_one(SourceModel, retry_source_id).state == {}
    retry_job = await _run_job(retry_source_id)
    assert retry_job.status == JobStatus.FINISHED
    assert retry_job.state["items"] == {
      "created": 1,
      "updated": 0,
      "unchanged": 1,
      "skipped": 1,
    }
  finally:
    _cleanup(source_ids)
    await server.close()


async def _exercise_atom_and_failures() -> None:
  server = FeedHTTPDouble()
  await server.start()
  source_ids: set[int] = set()
  try:
    source_id = _create_source(
      "extensions.rss.atom.Source",
      FeedSourceConfig(
        feed_url=f"{server.base_url}/atom.xml",
        fetch_full_text=False,
        download_enclosures=True,
      ),
    )
    source_ids.add(source_id)

    wrong_family_job = await _run_job(source_id)
    assert wrong_family_job.status == JobStatus.FAILED
    with TestSession() as db_session:
      source = db_session.get_one(SourceModel, source_id)
      assert source.state == {}

    server.atom_returns_wrong_family = False
    atom_job = await _run_job(source_id)
    assert atom_job.status == JobStatus.FINISHED
    feed_block, feed = _feed_root(source_id)
    assert feed.family == "atom"
    assert feed_block.id is not None
    items = _feed_items(feed_block.id)
    assert len(items) == 1
    item_id = items[0][0].id
    assert item_id is not None
    assert not read_relations(
      item_id,
      include_in=False,
      include_out=True,
      content=FULL_TEXT_RELATION,
    )
    enclosure = read_relations(
      item_id,
      include_in=False,
      include_out=True,
      content=ENCLOSURE_RELATION,
    )
    assert len(enclosure) == 1
    content = read_relations(
      enclosure[0].to_,
      include_in=False,
      include_out=True,
      content=CONTENT_RELATION,
    )
    assert len(content) == 1
    semantic = read_block(content[0].to_)
    assert semantic is not None
    assert semantic.resolver == "core.image.v1"
    assert await semantic.get_hydrated_content() == (ASSETS / "image.png").read_bytes()

    requests_before_invalid_job = server.feed_requests
    with pytest.raises(pydantic.ValidationError, match="config.full"):
      await _run_job(source_id, {"full": True})
    assert server.feed_requests == requests_before_invalid_job
  finally:
    _cleanup(source_ids)
    await server.close()


def test_rss_collection_reconciliation_watermark_conditional_and_manual_enclosure(
  async_runner,
):
  JobManager.start()
  register_core_resolvers()
  Extension._init_resolvers()
  Extension._init_sources()
  async_runner.run(SourceManager.sync_source_types_async())
  async_runner.run(JobManager.sync_job_types())
  async_runner.run(StorageManager.setup_builtin_storages_async())
  async_runner.run(_exercise_rss())


def test_atom_family_failure_and_automatic_enclosure_materialization(
  async_runner,
):
  JobManager.start()
  register_core_resolvers()
  Extension._init_resolvers()
  Extension._init_sources()
  async_runner.run(SourceManager.sync_source_types_async())
  async_runner.run(JobManager.sync_job_types())
  async_runner.run(StorageManager.setup_builtin_storages_async())
  async_runner.run(_exercise_atom_and_failures())
