"""GraphQL pagination must produce one complete snapshot or an explicit failure."""

import asyncio
import json
import typing

import httpx
import pytest

from extensions.github.adapter import GitHubGraphQLAdapter, GitHubGraphQLError


ACCOUNT: dict[str, typing.Any] = {
  "id": "U1",
  "login": "reader",
  "url": "https://github.com/reader",
}


def _connection(nodes, cursor=None):
  return {
    "nodes": nodes,
    "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
  }


def _repository(node_id):
  return {
    "id": node_id,
    "nameWithOwner": f"reader/{node_id}",
    "url": f"https://github.com/reader/{node_id}",
    "isPrivate": False,
    "isArchived": False,
    "repositoryTopics": {"nodes": []},
    "owner": {**ACCOUNT, "__typename": "User"},
  }


def _list(node_id, items):
  return {
    "id": node_id,
    "name": node_id,
    "slug": node_id,
    "isPrivate": False,
    "items": items,
  }


def _fetch(responses):
  requests = []

  def respond(request):
    body = json.loads(request.content)
    requests.append(body)
    assert len(requests) <= len(responses), "pagination did not terminate"
    return httpx.Response(200, json={"data": responses[len(requests) - 1]})

  async def run():
    async with httpx.AsyncClient(
      base_url="https://api.github.com", transport=httpx.MockTransport(respond)
    ) as client:
      async with GitHubGraphQLAdapter("test-token", client=client) as adapter:
        return await adapter.fetch_snapshot()

  return asyncio.run(run()), requests


def test_snapshot_traverses_stars_lists_and_embedded_items():
  snapshot, requests = _fetch(
    [
      {
        "viewer": {**ACCOUNT, "starredRepositories": _connection([_repository("R1")], "s1")}
      },
      {"viewer": {**ACCOUNT, "starredRepositories": _connection([_repository("R2")])}},
      {
        "viewer": {
          "lists": _connection([_list("L1", _connection([{"id": "R1"}], "i1"))], "l1")
        }
      },
      {"node": {"items": _connection([{"id": "R1"}, {"id": "R3"}])}},
      {"viewer": {"lists": _connection([_list("L2", _connection([]))])}},
      {"nodes": [_repository("R3")]},
    ]
  )
  assert snapshot.account.node_id == "U1"
  assert snapshot.starred_repository_node_ids == ("R1", "R2")
  assert {fact.repository.node_id for fact in snapshot.repositories} == {"R1", "R2", "R3"}
  assert [(fact.list.node_id, fact.repository_node_ids) for fact in snapshot.lists] == [
    ("L1", ("R1", "R3")),
    ("L2", ()),
  ]
  assert [request["variables"] for request in requests] == [
    {"cursor": None},
    {"cursor": "s1"},
    {"cursor": None},
    {"id": "L1", "cursor": "i1"},
    {"cursor": "l1"},
    {"ids": ["R3"]},
  ]


@pytest.mark.parametrize("level", ["stars", "lists", "items"])
@pytest.mark.parametrize("cursors", [("a", "a"), ("a", "b", "a")])
def test_repeated_cursor_rejects_incomplete_snapshot(level, cursors):
  responses = []
  if level != "stars":
    responses.append({"viewer": {**ACCOUNT, "starredRepositories": _connection([])}})
  for index, cursor in enumerate(cursors):
    connection = _connection([], cursor)
    if level == "stars":
      responses.append({"viewer": {**ACCOUNT, "starredRepositories": connection}})
    elif level == "lists":
      responses.append({"viewer": {"lists": connection}})
    elif index == 0:
      responses.append({"viewer": {"lists": _connection([_list("L1", connection)])}})
    else:
      responses.append({"node": {"items": connection}})
  with pytest.raises(GitHubGraphQLError, match="repeated endCursor"):
    _fetch(responses)


@pytest.mark.parametrize("cursor", [None, "", 42])
def test_missing_continuation_is_not_a_complete_snapshot(cursor):
  connection = _connection([])
  connection["pageInfo"] = {"hasNextPage": True, "endCursor": cursor}
  with pytest.raises(GitHubGraphQLError, match="omitted endCursor"):
    _fetch([{"viewer": {**ACCOUNT, "starredRepositories": connection}}])
