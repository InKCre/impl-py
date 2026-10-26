"""Recall candidates remain identifiable without changing the MCP read protocol."""

import asyncio
import datetime
import json

from mcp.client import Client
from mcp.server import MCPServer

from app.business.info_base.resolver import register_core_resolvers
from app.business.lexical_retrieval import LexicalRetrievalManager
from app.business.semantic_retrieval import SemanticRetrievalManager
from app.business.sink.mcp import MCPSink
from app.schemas.info_base.block import BlockModel
from app.schemas.info_base.relation import RelationModel
from app.schemas.lexical_retrieval import LexicalRetrievalMatch, LexicalRetrievalResult
from app.schemas.semantic_retrieval import (
  BlockSemanticRetrievalMatch,
  RelationSemanticRetrievalMatch,
  SemanticRetrievalResult,
)
from app.schemas.sink import SinkModel


def test_recall_labels_preserve_evidence_and_unreadable_candidates(monkeypatch):
  register_core_resolvers()
  now = datetime.datetime.now(datetime.UTC)
  blocks = [
    BlockModel(
      id=1,
      resolver="core.text.v1",
      content="Alpha document",
      created_at=now,
      updated_at=now,
    ),
    BlockModel(
      id=2, resolver="core.text.v1", content="Beta document", created_at=now, updated_at=now
    ),
    BlockModel(
      id=3, resolver="not.installed", content="opaque", created_at=now, updated_at=now
    ),
  ]

  async def lexical(*_args, **_kwargs):
    return LexicalRetrievalResult(
      matches=(
        LexicalRetrievalMatch(
          block=blocks[0],
          label="Alpha",
          excerpt="Alpha document",
          evidence="terms",
          rank=1,
        ),
      )
    )

  async def semantic(*_args, **_kwargs):
    return SemanticRetrievalResult(
      profile=1,
      matches=tuple(
        BlockSemanticRetrievalMatch(entity=block, score=0.8) for block in blocks
      )
      + (
        RelationSemanticRetrievalMatch(
          entity=RelationModel(id=4, from_=1, to_=2, content="supports", updated_at=now),
          score=0.7,
        ),
      ),
    )

  monkeypatch.setattr(LexicalRetrievalManager, "retrieve", lexical)
  monkeypatch.setattr(SemanticRetrievalManager, "retrieve", semantic)
  sink = MCPSink(SinkModel(id=1, type="core.mcp.v1", config={"pat": "test-only"}))
  server = MCPServer("recall-candidates")
  sink._register_tools(server)

  async def scenario():
    async with Client(server, mode="legacy") as client:
      response = await client.call_tool(
        "inkcre_recall", {"query": "document", "modes": ["lexical", "semantic"]}
      )
      assert not response.is_error
      data = response.structured_content
      assert data is not None
      assert json.loads(response.content[0].model_dump()["text"]) == data
      candidates = {item["entity"]: item for item in data["results"]}
      assert candidates["block:1"]["label"] == "Alpha"
      assert [item["mode"] for item in candidates["block:1"]["evidence"]] == [
        "lexical",
        "semantic",
      ]
      assert "Beta document" in candidates["block:2"]["label"]
      assert candidates["block:3"]["label"] is None
      assert candidates["block:3"]["evidence"] == [{"mode": "semantic", "score": 0.8}]
      assert candidates["relation:4"]["label"] == "block:1 → supports → block:2"
      assert data["mode_errors"] == []

  asyncio.run(scenario())
