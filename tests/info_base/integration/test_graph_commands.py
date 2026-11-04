"""Real PostgreSQL proof for flat GraphForm submission semantics."""

import asyncio
import os
import uuid

import pytest
import sqlalchemy
import sqlalchemy.exc
import psycopg.errors
import sqlmodel

from inkcre_core.business.info_base.commands import (
  persist_graph,
  submit_graph,
  submit_stars,
)
from inkcre_core.persistence.info_base.uow import graph_uow
from inkcre_core.business.info_base.resolver import register_core_resolvers
from tests.database import TestSession
from inkcre_core.schemas.info_base.block import BlockForm, BlockModel
from inkcre_core.schemas.info_base.main import (
  GraphBlockForm,
  GraphForm,
  GraphRelationForm,
  InArcForm,
  OutArcForm,
  StarsGraphForm,
)
from inkcre_core.schemas.info_base.relation import RelationForm, RelationModel


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)


def _cleanup(block_ids: list[int]) -> None:
  if not block_ids:
    return
  with TestSession() as db:
    db.connection().execute(
      sqlalchemy.text("DELETE FROM inkcre.blocks WHERE id = ANY(:ids)"),
      {"ids": block_ids},
    )
    db.commit()


def test_submit_graph_creates_arbitrary_links_and_returns_only_block_mapping(async_runner):
  persisted: list[int] = []
  graph = GraphForm(
    blocks=(
      GraphBlockForm(id=-4, resolver="core.text.v1", content="first"),
      GraphBlockForm(id=-5, resolver="core.text.v1", content="second"),
    ),
    relations=(
      GraphRelationForm(from_=-4, to_=-5, content="next"),
      GraphRelationForm(from_=-5, to_=-4, content="reference"),
    ),
  )

  try:
    result = async_runner.run(submit_graph(graph))
    persisted = [mapping.id for mapping in result.blocks]
    mapping = {item.local_id: item.id for item in result.blocks}

    assert result.model_dump() == {
      "blocks": (
        {"local_id": -4, "id": mapping[-4]},
        {"local_id": -5, "id": mapping[-5]},
      )
    }
    with TestSession() as db:
      blocks = tuple(db.get(BlockModel, block_id) for block_id in persisted)
      relations = db.exec(
        sqlmodel.select(RelationModel).where(RelationModel.from_.in_(persisted))  # pyrefly: ignore[missing-attribute]
      ).all()
    assert [block.content for block in blocks if block is not None] == [
      "first",
      "second",
    ]
    assert {(item.from_, item.content, item.to_) for item in relations} == {
      (mapping[-4], "next", mapping[-5]),
      (mapping[-5], "reference", mapping[-4]),
    }
  finally:
    _cleanup(persisted)


def test_repeating_negative_ids_requests_new_blocks_each_time(async_runner):
  persisted: list[int] = []
  graph = GraphForm(
    blocks=(GraphBlockForm(id=-1, resolver="core.text.v1", content="repeat"),)
  )

  try:
    first = async_runner.run(submit_graph(graph)).blocks[0].id
    second = async_runner.run(submit_graph(graph)).blocks[0].id
    persisted.extend((first, second))

    assert first != second
  finally:
    _cleanup(persisted)


def test_recursive_stars_authoring_retains_reconciliation_and_direction(async_runner):
  register_core_resolvers()
  marker = uuid.uuid4().hex
  contents = {
    "root": f"{marker}:root",
    "out": f"{marker}:out",
    "in": f"{marker}:in",
  }
  stars = StarsGraphForm(
    block=BlockForm(resolver="core.text.v1", content=contents["root"]),
    out_arcs=(
      OutArcForm(
        relation=RelationForm(content="outgoing"),
        to_graph=StarsGraphForm(
          block=BlockForm(resolver="core.text.v1", content=contents["out"])
        ),
      ),
    ),
    in_arcs=(
      InArcForm(
        relation=RelationForm(content="incoming"),
        from_graph=StarsGraphForm(
          block=BlockForm(resolver="core.text.v1", content=contents["in"])
        ),
      ),
    ),
  )

  try:
    first_root = async_runner.run(submit_stars(stars))
    assert first_root.id is not None
    second_root = async_runner.run(submit_stars(stars))
    assert second_root.id == first_root.id

    with TestSession() as db:
      blocks = db.exec(
        sqlmodel.select(BlockModel).where(BlockModel.content.in_(contents.values()))  # pyrefly: ignore[missing-attribute]
      ).all()
      block_ids = [block.id for block in blocks if block.id is not None]
      by_content = {block.content: block.id for block in blocks}
      relations = db.exec(
        sqlmodel.select(RelationModel).where(
          RelationModel.from_.in_(block_ids)  # pyrefly: ignore[missing-attribute]
        )
      ).all()

    assert len(blocks) == 3
    assert {(item.from_, item.content, item.to_) for item in relations} == {
      (by_content[contents["root"]], "outgoing", by_content[contents["out"]]),
      (by_content[contents["in"]], "incoming", by_content[contents["root"]]),
    }
  finally:
    with TestSession() as db:
      cleanup_ids = [
        block.id
        for block in db.exec(
          sqlmodel.select(BlockModel).where(
            BlockModel.content.in_(contents.values())  # pyrefly: ignore[missing-attribute]
          )
        ).all()
        if block.id is not None
      ]
    _cleanup(cleanup_ids)


def test_composed_graph_failure_rolls_back_earlier_graph(async_runner):
  marker = uuid.uuid4().hex
  with TestSession() as db:
    assert db.get(BlockModel, 2_147_483_647) is None

  async def compose():
    async with graph_uow() as uow:
      created = await persist_graph(
        GraphForm(blocks=(GraphBlockForm(id=-1, resolver="core.text.v1", content=marker),)),
        uow,
      )
      await persist_graph(
        GraphForm(
          relations=(
            GraphRelationForm(
              from_=created.blocks[0].id, to_=2_147_483_647, content="invalid target"
            ),
          )
        ),
        uow,
      )

  with pytest.raises(sqlalchemy.exc.IntegrityError) as error:
    async_runner.run(compose())
  assert isinstance(error.value.orig, psycopg.errors.ForeignKeyViolation)
  with TestSession() as db:
    assert (
      db.exec(sqlmodel.select(BlockModel).where(BlockModel.content == marker)).first()
      is None
    )


def test_cancelled_graph_operation_rolls_back_and_releases_connection(async_runner):
  marker = uuid.uuid4().hex

  async def cancel_then_retry():
    with pytest.raises(asyncio.CancelledError):
      async with graph_uow() as uow:
        await persist_graph(
          GraphForm(
            blocks=(GraphBlockForm(id=-1, resolver="core.text.v1", content=marker),)
          ),
          uow,
        )
        raise asyncio.CancelledError
    # A later operation must be able to use the pool after cancellation cleanup.
    return await submit_graph(
      GraphForm(blocks=(GraphBlockForm(id=-1, resolver="core.text.v1", content=marker),))
    )

  result = async_runner.run(cancel_then_retry())
  persisted = [item.id for item in result.blocks]
  try:
    with TestSession() as db:
      blocks = db.exec(
        sqlmodel.select(BlockModel).where(BlockModel.content == marker)
      ).all()
      assert [block.id for block in blocks] == persisted
  finally:
    _cleanup(persisted)


def test_blob_graph_transaction_and_hydration(async_runner):
  from inkcre_core.business.info_base.storage import StorageManager, WritableStorage
  from inkcre_core.business.info_base.services import BlockService
  from inkcre_core.schemas.info_base.storage import StorageBlobModel

  async def scenario():
    await StorageManager.setup_builtin_storages_async()
    storage = await StorageManager.get_storage_async(-4)
    assert isinstance(storage, WritableStorage)
    persisted_id = None
    pointer = None
    try:
      async with graph_uow() as uow:
        pointer = await storage.create_content(b"transactional bytes", uow.storage)
        block = await uow.blocks.create(
          BlockForm(storage=-4, resolver="core.file.v1", content=pointer)
        )
        persisted_id = block.id
      assert persisted_id is not None
      loaded = await BlockService.get(persisted_id)
      assert loaded is not None
      assert await loaded.get_hydrated_content() == b"transactional bytes"

      # The byte write participates in the same rollback as the graph operation.
      rolled_back_pointer = None
      with pytest.raises(RuntimeError, match="abort graph"):
        async with graph_uow() as uow:
          rolled_back_pointer = await storage.create_content(b"not committed", uow.storage)
          raise RuntimeError("abort graph")
      from inkcre_core.business.info_base.storage.postgresql import PostgreSQLBlobPointer

      blob_id = PostgreSQLBlobPointer.model_validate_json(rolled_back_pointer).blob_id
      with TestSession() as db:
        assert db.get(StorageBlobModel, blob_id) is None
    finally:
      async with graph_uow() as uow:
        if persisted_id is not None:
          await uow.blocks.delete(persisted_id)
        if pointer is not None:
          await storage.delete_content(pointer, uow.storage)

  async_runner.run(scenario())


def test_github_stars_retain_node_identity_and_reject_ambiguity(async_runner):
  from extensions.github.resolver import GitHubAccountResolver, GitHubGraphIntegrityError
  from extensions.github.schema import GitHubAccount
  from inkcre_core.business.info_base.commands import persist_stars

  account = GitHubAccount(
    node_id=uuid.uuid4().hex, kind="user", login="before", url="https://github.com/before"
  )
  changed = account.model_copy(update={"login": "after"})

  async def scenario():
    # The deliberate ambiguity aborts this entire test operation, including its rows.
    with pytest.raises(GitHubGraphIntegrityError, match="multiple Blocks"):
      async with graph_uow() as uow:
        first = await persist_stars(GitHubAccountResolver.create_graph(account), uow)
        again = await persist_stars(GitHubAccountResolver.create_graph(changed), uow)
        assert again.id == first.id
        await uow.blocks.create(GitHubAccountResolver.create_block(changed))
        await persist_stars(GitHubAccountResolver.create_graph(account), uow)

  async_runner.run(scenario())


def test_async_entity_routes_preserve_crud_and_hydrated_projection(async_runner):
  import fastapi
  import httpx
  from app.routes.block import ROUTER as blocks
  from app.routes.entities import ROUTER as entities
  from app.routes.relation import ROUTER as relations

  app = fastapi.FastAPI()
  for router in (blocks, entities, relations):
    app.include_router(router)

  async def scenario():
    ids = []
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
      try:
        for content in ("first", "second"):
          response = await client.post(
            "/blocks", json={"resolver": "core.text.v1", "content": content}
          )
          assert response.status_code == 201
          ids.append(response.json()["id"])
        relation = await client.post(
          "/relations", json={"from_": ids[0], "to_": ids[1], "content": "next"}
        )
        assert relation.status_code == 200
        patched = await client.patch(f"/blocks/{ids[0]}", json={"content": "updated"})
        assert patched.status_code == 200
        response = await client.post(
          "/entities/get",
          json={
            "entities": [{"type": "block", "id": ids[0]}],
            "content": "hydrated",
          },
        )
        assert response.status_code == 200
        assert response.json()[0]["hydrated_content"] == "updated"
      finally:
        for block_id in ids:
          response = await client.delete(f"/blocks/{block_id}")
          assert response.status_code == 204

  async_runner.run(scenario())
