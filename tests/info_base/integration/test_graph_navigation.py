"""Persisted graph navigation: page bounds must not become false disconnection."""

import os

import pytest

from inkcre_core.business.graph_navigation_retrieval import GraphNavigationRetrievalManager
from inkcre_core.persistence.info_base.uow import graph_uow
from inkcre_core.schemas.info_base.block import BlockForm
from inkcre_core.schemas.info_base.relation import RelationCreateForm


pytestmark = [
  pytest.mark.integration,
  pytest.mark.skipif(
    not os.getenv("INKCRE_TEST_DATABASE_URL"),
    reason="requires an explicitly selected migrated disposable PostgreSQL runtime",
  ),
]


@pytest.fixture
def navigation_graph(async_runner):
  names = ("start", "bridge", "target", "detour", "isolated")

  async def create():
    async with graph_uow() as uow:
      blocks = await uow.blocks.create_many(
        BlockForm(resolver="core.text.v1", content=name) for name in names
      )
      ids = {
        name: block.id
        for name, block in zip(names, blocks, strict=True)
        if block.id is not None
      }
      assert len(ids) == len(names)
      await uow.relations.create_many(
        RelationCreateForm(from_=ids[left], to_=ids[right], content=content)
        for left, right, content in (
          ("start", "bridge", "links"),
          ("bridge", "target", "links"),
          ("start", "detour", "quotes"),
          ("detour", "target", "links"),
          ("bridge", "start", "links"),
          ("bridge", "bridge", "links"),
        )
      )
      return ids

  ids = async_runner.run(create())
  try:
    yield ids
  finally:

    async def cleanup():
      async with graph_uow() as uow:
        for block_id in ids.values():
          await uow.blocks.delete(block_id)

    async_runner.run(cleanup())


def test_neighborhood_pages_keep_direction_and_close_endpoints(
  async_runner, navigation_graph
):
  ids = navigation_graph

  async def scenario():
    cursor, rows = None, []
    while True:
      page = await GraphNavigationRetrievalManager.get_block_neighborhood(
        ids["bridge"], limit=2, cursor=cursor
      )
      assert page is not None
      block_ids = {block.id for block in page.graph.blocks}
      assert ids["bridge"] in block_ids
      assert all(
        edge.from_ in block_ids and edge.to_ in block_ids for edge in page.graph.relations
      )
      assert page.graph.relations
      rows.extend(page.graph.relations)
      assert len({edge.id for edge in rows}) == len(rows)
      cursor = page.next_cursor
      if cursor is None:
        break
    assert len(rows) == 4
    assert [edge.id for edge in rows] == sorted({edge.id for edge in rows}, reverse=True)
    assert {(edge.from_, edge.to_) for edge in rows} == {
      (ids["start"], ids["bridge"]),
      (ids["bridge"], ids["target"]),
      (ids["bridge"], ids["start"]),
      (ids["bridge"], ids["bridge"]),
    }
    filtered = await GraphNavigationRetrievalManager.get_block_neighborhood(
      ids["start"], direction="out", contents=("quotes",)
    )
    assert filtered is not None
    assert [(edge.from_, edge.to_, edge.content) for edge in filtered.graph.relations] == [
      (ids["start"], ids["detour"], "quotes")
    ]
    isolated = await GraphNavigationRetrievalManager.get_block_neighborhood(ids["isolated"])
    assert isolated is not None
    assert [block.id for block in isolated.graph.blocks] == [ids["isolated"]]
    assert isolated.graph.relations == ()

  async_runner.run(scenario())


def test_paths_preserve_persisted_direction_and_distinguish_exhaustion_from_limits(
  async_runner, navigation_graph
):
  ids = navigation_graph

  async def scenario():
    for start, target, direction, expected in (
      ("start", "target", "out", ("start", "bridge", "target")),
      ("target", "start", "in", ("target", "bridge", "start")),
      ("bridge", "bridge", "both", ("bridge",)),
    ):
      result = await GraphNavigationRetrievalManager.find_path(
        ids[start], ids[target], direction=direction, contents=("links",), max_hops=2
      )
      assert result.status == "found"
      assert result.block_path == tuple(ids[name] for name in expected)
      assert len(result.relation_path) == len(expected) - 1
      assert {block.id for block in result.graph.blocks} == set(result.block_path)
      assert {edge.id for edge in result.graph.relations} == set(result.relation_path)
      if start != target:
        edges = {edge.id: edge for edge in result.graph.relations}
        persisted_path = [
          (edges[edge_id].from_, edges[edge_id].to_, edges[edge_id].content)
          for edge_id in result.relation_path
        ]
        forward = [
          (ids["start"], ids["bridge"], "links"),
          (ids["bridge"], ids["target"], "links"),
        ]
        assert persisted_path == (forward if direction == "out" else forward[::-1])
    bounded = await GraphNavigationRetrievalManager.find_path(
      ids["start"], ids["target"], direction="out", max_hops=1
    )
    assert bounded.status == "limit_reached"
    exhausted = await GraphNavigationRetrievalManager.find_path(
      ids["start"], ids["isolated"], direction="out", max_hops=8
    )
    assert exhausted.status == "not_found"

  async_runner.run(scenario())


def test_parallel_relations_do_not_hide_an_older_shortest_path(async_runner):
  async def scenario():
    async with graph_uow() as uow:
      blocks = await uow.blocks.create_many(
        BlockForm(resolver="core.text.v1", content=name)
        for name in ("path start", "parallel-edge neighbor", "path target")
      )
    start, neighbor, target = (block.id for block in blocks)
    assert start is not None and neighbor is not None and target is not None
    try:
      async with graph_uow() as uow:
        direct = await uow.relations.create_many(
          (RelationCreateForm(from_=start, to_=target, content="links"),)
        )
        # Distinct persisted Relations can share endpoints. The oldest edge is
        # behind a complete repository page while only three Blocks are explored.
        await uow.relations.create_many(
          RelationCreateForm(from_=start, to_=neighbor, content=f"evidence {index}")
          for index in range(10_000)
        )
      result = await GraphNavigationRetrievalManager.find_path(
        start, target, direction="out", max_hops=2, max_explored_blocks=3
      )
      assert result.status == "found"
      assert result.block_path == (start, target)
      assert result.relation_path == (direct[0].id,)
      assert {block.id for block in result.graph.blocks} == {start, target}
      assert [(edge.from_, edge.to_, edge.content) for edge in result.graph.relations] == [
        (start, target, "links")
      ]
    finally:
      async with graph_uow() as uow:
        for block in blocks:
          assert block.id is not None
          await uow.blocks.delete(block.id)

  async_runner.run(scenario())
