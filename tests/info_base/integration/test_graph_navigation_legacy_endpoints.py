"""Graph traversal ignores persisted Relations with missing endpoints."""

import os
import uuid

import pytest
from sqlalchemy import text

from inkcre_core.business.graph_navigation_retrieval import GraphNavigationRetrievalManager
from inkcre_core.schemas.info_base.block import BlockForm
from tests.database import TestSession, seed_blocks, seed_relation


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)


@pytest.fixture
def graph_with_partial_relations():
  content = f"partial-relation-{uuid.uuid4()}"
  with TestSession() as db, db.begin():
    start, end = seed_blocks(
      (
        BlockForm(resolver="core.text.v1", content=f"{content}:start"),
        BlockForm(resolver="core.text.v1", content=f"{content}:end"),
      ),
      db,
    )
    assert start.id is not None and end.id is not None
    path = seed_relation(start.id, end.id, content, db)
    assert path.id is not None
    db.connection().execute(
      text(
        "INSERT INTO inkcre.relations (from_, to_, content) VALUES "
        "(:start, NULL, :content), (NULL, :end, :content)"
      ),
      {"start": start.id, "end": end.id, "content": content},
    )
  try:
    yield start.id, end.id, path.id, content
  finally:
    with TestSession() as db, db.begin():
      db.connection().execute(
        text("DELETE FROM inkcre.blocks WHERE id IN (:start, :end)"),
        {"start": start.id, "end": end.id},
      )


@pytest.mark.parametrize("direction", ["in", "out"])
def test_path_budget_counts_real_blocks(
  async_runner, graph_with_partial_relations, direction
):
  start, end, relation, content = graph_with_partial_relations
  if direction == "in":
    start, end = end, start
  result = async_runner.run(
    GraphNavigationRetrievalManager.find_path(
      start, end, direction=direction, contents=(content,), max_explored_blocks=2
    )
  )
  assert result.status == "found"
  assert result.block_path == (start, end)
  assert result.relation_path == (relation,)


def test_connected_components_exclude_incomplete_relations(
  async_runner, graph_with_partial_relations
):
  start, end, relation, content = graph_with_partial_relations
  result = async_runner.run(
    GraphNavigationRetrievalManager.get_connected_components(
      (start, end), contents=(content,), max_explored_blocks=2
    )
  )
  assert not result.truncated
  assert [item.member_block_ids for item in result.components] == [(start, end)]
  assert [item.id for item in result.proof_graph.relations] == [relation]
