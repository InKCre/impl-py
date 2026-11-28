"""Only a complete GitHub observation may retire current Stars and Lists."""

import json
import os
from uuid import uuid4

import httpx
import pytest

from extensions.github.stars import Source
from inkcre_core.business.source import SourceManager
from inkcre_core.persistence.source.uow import source_uow
from inkcre_core.schemas.job import JobStatus


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires a migrated disposable database",
)


def test_partial_page_preserves_last_complete_stars_and_lists(
  async_runner, monkeypatch, run_source_job
):
  identity = uuid4().hex
  viewer_id, repo_id, list_id = [
    f"{kind}-{identity}" for kind in ("viewer", "repo", "list")
  ]
  viewer = {"id": viewer_id, "login": "reader", "url": "https://example.test/reader"}
  repository = {
    "id": repo_id,
    "nameWithOwner": "reader/book",
    "url": "https://example.test/reader/book",
    "owner": {**viewer, "__typename": "User"},
    "repositoryTopics": {"nodes": []},
    "isPrivate": False,
    "isArchived": False,
  }
  page = {"hasNextPage": False, "endCursor": None}
  saved_list = {
    "id": list_id,
    "name": "Reading",
    "slug": "reading",
    "isPrivate": False,
    "items": {"nodes": [{"id": repo_id}], "pageInfo": page},
  }
  snapshot = "initial"

  async def graphql(_transport, request: httpx.Request):
    assert request.url == "https://api.github.com/graphql"
    payload = json.loads(request.content)
    if "starredRepositories(" in payload["query"]:
      stars = [repository] if snapshot == "initial" else []
      result = {
        "viewer": {**viewer, "starredRepositories": {"nodes": stars, "pageInfo": page}}
      }
    elif "lists(" in payload["query"]:
      if payload["variables"]["cursor"] == "next-list-page":
        return httpx.Response(200, json={"errors": [{"message": "page unavailable"}]})
      pagination = (
        {"hasNextPage": True, "endCursor": "next-list-page"}
        if snapshot == "partial"
        else page
      )
      result = {
        "viewer": {
          "lists": {
            "nodes": [saved_list] if snapshot == "initial" else [],
            "pageInfo": pagination,
          }
        }
      }
    else:
      raise AssertionError("unexpected GraphQL operation")
    return httpx.Response(200, json={"data": result})

  monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", graphql)
  source_type = f"{Source.__module__}.{Source.__name__}"
  async_runner.run(SourceManager.sync_source_types_async())
  source = async_runner.run(
    SourceManager.create(source_type, config={"github_token": "fixture"})
  )
  assert source.id is not None
  source_id = source.id
  identities = (
    ("extensions.github.account.v1", viewer_id),
    ("extensions.github.repository.v1", repo_id),
    ("extensions.github.list.v1", list_id),
  )

  async def observed_graph():
    async with source_uow() as uow:
      ids = []
      for resolver, node_id in identities:
        (block,) = await uow.graph.blocks.find_json_field(resolver, "node_id", node_id)
        assert block.id is not None
        ids.append(block.id)
      viewer_block, repo_block, list_block = ids
      facts = {
        (relation.from_, relation.content, relation.to_)
        for relation in await uow.graph.relations.get_for_endpoints(
          set(ids), contents=("stars", "contains", "owns", "collects")
        )
      }
      pinned = await uow.sources.get(source_id)
      assert pinned is not None and pinned.block is not None
      assert pinned.state["account_node_id"] == viewer_id
      assert (pinned.block, "collects", viewer_block) in facts
      membership = {
        (viewer_block, "stars", repo_block),
        (viewer_block, "owns", list_block),
        (list_block, "contains", repo_block),
      }
      return ids, membership & facts

  async def cleanup():
    pinned = await SourceManager.get(source_id)
    await SourceManager.delete(source_id)
    async with source_uow() as uow:
      if pinned is not None and pinned.block is not None:
        await uow.graph.blocks.delete(pinned.block)
      for resolver, node_id in identities:
        for block in await uow.graph.blocks.find_json_field(resolver, "node_id", node_id):
          assert block.id is not None
          await uow.graph.blocks.delete(block.id)

  try:
    first = async_runner.run(run_source_job(source_id))
    assert first.status == JobStatus.FINISHED, first.state
    block_ids, initial_memberships = async_runner.run(observed_graph())
    assert len(initial_memberships) == 3

    snapshot = "partial"
    failed = async_runner.run(run_source_job(source_id))
    assert failed.status == JobStatus.FAILED and failed.state["error"] == "page unavailable"
    assert async_runner.run(observed_graph()) == (block_ids, initial_memberships)

    snapshot = "empty"
    complete = async_runner.run(run_source_job(source_id))
    assert complete.status == JobStatus.FINISHED, complete.state
    assert async_runner.run(observed_graph()) == (block_ids, set())
  finally:
    async_runner.run(cleanup())
