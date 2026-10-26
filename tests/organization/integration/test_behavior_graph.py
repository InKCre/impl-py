"""Real PostgreSQL journey across exact Organization graph effects."""

from tests.database import seed_block, seed_blocks, seed_relation


import os
import uuid

import pytest
import sqlalchemy

from app.business.graph_navigation_retrieval import GraphNavigationRetrievalManager
from app.business.info_base.resolver import register_core_resolvers
from app.business.organization import (
  DuplicateAssertionBehaviorResolver,
  EvidenceStanceBehaviorResolver,
  ExistingReferentAnchoringBehaviorResolver,
  RefinementBehaviorResolver,
  SupersessionBehaviorResolver,
  SynthesisBehaviorResolver,
  register_core_organization_behaviors,
)
from tests.database import TestSession
from app.schemas.info_base.block import BlockForm


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)


def _cleanup(block_ids: list[int]) -> None:
  if not block_ids:
    return
  with TestSession() as db_session:
    db_session.connection().execute(
      sqlalchemy.text("DELETE FROM inkcre.blocks WHERE id = ANY(:ids)"),
      {"ids": block_ids},
    )
    db_session.commit()


def test_exact_behaviors_compose_into_replayable_graph_use(async_runner) -> None:
  register_core_resolvers()
  register_core_organization_behaviors()
  marker = uuid.uuid4().hex
  persisted: list[int] = []

  try:
    with TestSession() as db_session:
      blocks = seed_blocks(
        (
          BlockForm(resolver="core.text.v1", content=f"{marker}: old limit"),
          BlockForm(resolver="core.text.v1", content=f"{marker}: new limit"),
          BlockForm(resolver="core.text.v1", content=f"{marker}: detail about the service"),
          BlockForm(resolver="core.text.v1", content=f"{marker}: measurement"),
          BlockForm(resolver="core.text.v1", content=f"{marker}: service record"),
          BlockForm(resolver="core.text.v1", content=f"{marker}: copied report"),
          BlockForm(resolver="core.text.v1", content=f"{marker}: relayed copy"),
        ),
        db_session,
      )
      ids = tuple(block.id for block in blocks if block.id is not None)
      assert len(ids) == 7
      persisted.extend(ids)
      old, new, detail, evidence, referent, copied, relayed = ids

      db_session.commit()

    supersession = async_runner.run(
      SupersessionBehaviorResolver.record_supersession(
        new,
        old,
      )
    )
    refinement = async_runner.run(
      RefinementBehaviorResolver.record_refinement(
        detail,
        old,
      )
    )
    stance = async_runner.run(
      EvidenceStanceBehaviorResolver.record_evidence_stance(
        evidence,
        new,
        "supports",
      )
    )
    first_anchor = async_runner.run(
      ExistingReferentAnchoringBehaviorResolver.anchor_existing_referent(
        detail,
        "the service",
        referent,
      )
    )
    second_anchor = async_runner.run(
      ExistingReferentAnchoringBehaviorResolver.anchor_existing_referent(
        detail,
        "the service",
        referent,
      )
    )
    first_duplicate = async_runner.run(
      DuplicateAssertionBehaviorResolver.record_duplicate_assertion(
        copied,
        relayed,
      )
    )
    second_duplicate = async_runner.run(
      DuplicateAssertionBehaviorResolver.record_duplicate_assertion(
        relayed,
        old,
      )
    )
    synthesis = async_runner.run(
      SynthesisBehaviorResolver.create_synthesis(
        f"{marker}: reusable synthesis",
        (new, detail, evidence),
      )
    )
    replay = async_runner.run(
      SynthesisBehaviorResolver.create_synthesis(
        f"{marker}: reusable synthesis",
        (evidence, detail, new),
      )
    )
    changed = async_runner.run(
      SynthesisBehaviorResolver.create_synthesis(
        f"{marker}: changed synthesis",
        (new, detail, evidence),
        synthesis.synthesis_block_id,
      )
    )

    assert supersession.created
    assert refinement.created
    assert stance.created
    assert first_anchor.fragment_created
    assert second_anchor.fragment_block_id == first_anchor.fragment_block_id
    assert not second_anchor.fragment_created
    assert not second_anchor.refers_to.created
    assert first_duplicate.created and second_duplicate.created
    assert replay.synthesis_block_id == synthesis.synthesis_block_id
    assert not replay.synthesis_created
    assert changed.synthesis_block_id != synthesis.synthesis_block_id
    assert changed.edited is not None and changed.edited.created
    persisted.extend(
      (
        first_anchor.fragment_block_id,
        synthesis.synthesis_block_id,
        changed.synthesis_block_id,
      )
    )

    with pytest.raises(ValueError, match="existing supersedes path"):
      async_runner.run(
        SupersessionBehaviorResolver.record_supersession(
          old,
          new,
        )
      )
    with pytest.raises(ValueError, match="opposite stance"):
      async_runner.run(
        EvidenceStanceBehaviorResolver.record_evidence_stance(
          evidence,
          new,
          "challenges",
        )
      )

    duplicate_components = async_runner.run(
      GraphNavigationRetrievalManager.get_connected_components(
        (copied, old),
        contents=("duplicates assertion",),
      )
    )
    assert duplicate_components.missing_seed_block_ids == ()
    assert not duplicate_components.truncated
    assert duplicate_components.components[0].seed_block_ids == (copied, old)
    assert set(duplicate_components.components[0].member_block_ids) == {
      copied,
      relayed,
      old,
    }
    assert len(duplicate_components.proof_graph.relations) == 2
  finally:
    _cleanup(persisted)


def test_connected_component_reports_missing_and_bounded_incomplete_proof(
  async_runner,
) -> None:
  marker = uuid.uuid4().hex
  persisted: list[int] = []
  try:
    blocks = [
      seed_block(BlockForm(resolver="core.text.v1", content=f"{marker}:{index}"))
      for index in range(3)
    ]
    ids = tuple(block.id for block in blocks if block.id is not None)
    assert len(ids) == 3
    persisted.extend(ids)
    left, bridge, right = ids

    seed_relation(left, bridge, "duplicates assertion")
    seed_relation(bridge, right, "duplicates assertion")

    result = async_runner.run(
      GraphNavigationRetrievalManager.get_connected_components(
        (left, right, max(ids) + 1_000_000),
        contents=("duplicates assertion",),
        max_explored_blocks=3,
        max_explored_relations=1,
      )
    )
    assert result.truncated
    assert result.missing_seed_block_ids == (max(ids) + 1_000_000,)
    assert len(result.proof_graph.relations) <= 1
  finally:
    _cleanup(persisted)


def test_synthesis_rolls_back_block_and_basis_on_write_failure(async_runner, monkeypatch):
  from app.persistence.info_base.repository import RelationRepository
  from app.persistence.info_base.uow import graph_uow

  marker = uuid.uuid4().hex

  async def prepare():
    async with graph_uow() as uow:
      return await uow.blocks.create_many(
        tuple(
          BlockForm(resolver="core.text.v1", content=f"{marker}:{index}")
          for index in range(2)
        )
      )

  blocks = async_runner.run(prepare())
  ids = [block.id for block in blocks if block.id is not None]
  original = RelationRepository.fetchsert
  writes = 0

  async def fail_after_second_write(self, relation):
    nonlocal writes
    result = await original(self, relation)
    writes += 1
    if writes == 2:
      raise RuntimeError("injected basis failure")
    return result

  async def verify_rollback():
    async with graph_uow() as uow:
      assert await uow.blocks.matching_content("core.text.v1", marker) == ()
      assert await uow.relations.get_outgoing_many(ids) == ()
      assert len(await uow.blocks.get_many(ids)) == 2

  try:
    monkeypatch.setattr(RelationRepository, "fetchsert", fail_after_second_write)
    with pytest.raises(RuntimeError, match="injected basis failure"):
      async_runner.run(SynthesisBehaviorResolver.create_synthesis(marker, ids))
    assert writes == 2
    async_runner.run(verify_rollback())
  finally:
    _cleanup(ids)


def test_organization_compares_text_not_storage_pointers(async_runner):
  from app.business.info_base.resolver import ResolverManager
  from app.persistence.info_base.uow import graph_uow

  register_core_resolvers()
  marker = uuid.uuid4().hex
  persisted = []
  blob_id = None

  async def run():
    nonlocal blob_id
    async with graph_uow() as uow:
      blob_id = await uow.storage.create_blob(f"{marker}: the actual service".encode())
      pointer = f'{{"blob_id":"{blob_id}"}}'
      stored = await uow.blocks.create(
        BlockForm(resolver="core.text.v1", storage=-4, content=pointer)
      )
      source = await uow.blocks.create(
        BlockForm(resolver="core.text.v1", content=f"{marker}: pointer example {pointer}")
      )
      referent = await uow.blocks.create(
        BlockForm(resolver="core.text.v1", content=f"{marker}: service identity")
      )
      assert stored.id is not None and source.id is not None and referent.id is not None
      persisted.extend((stored.id, source.id, referent.id))
      await uow.relations.create(source.id, stored.id, "has mention")
      await uow.relations.create(stored.id, referent.id, "refers to")
      await uow.relations.create(source.id, stored.id, "synthesis")
      await uow.relations.create(referent.id, stored.id, "synthesis")

    with pytest.raises(ValueError, match="does not occur"):
      await ExistingReferentAnchoringBehaviorResolver.anchor_existing_referent(
        stored.id, pointer, referent.id
      )
    with pytest.raises(ValueError, match="does not occur"):
      await ExistingReferentAnchoringBehaviorResolver.anchor_existing_referent(
        source.id, "an invented mention", referent.id
      )

    real_mention = await ExistingReferentAnchoringBehaviorResolver.anchor_existing_referent(
      stored.id, "the actual service", referent.id
    )
    persisted.append(real_mention.fragment_block_id)
    pointer_mention = (
      await ExistingReferentAnchoringBehaviorResolver.anchor_existing_referent(
        source.id, pointer, referent.id
      )
    )
    persisted.append(pointer_mention.fragment_block_id)
    assert pointer_mention.fragment_block_id != stored.id
    synthesis = await SynthesisBehaviorResolver.create_synthesis(
      pointer, (source.id, referent.id)
    )
    persisted.append(synthesis.synthesis_block_id)
    assert synthesis.synthesis_block_id != stored.id
    replay = await SynthesisBehaviorResolver.create_synthesis(
      pointer, (referent.id, source.id)
    )
    assert replay.synthesis_block_id == synthesis.synthesis_block_id
    async with graph_uow() as uow:
      result = await uow.blocks.get(synthesis.synthesis_block_id)
      assert result is not None and result.storage is None
    assert await ResolverManager.get(result).get_text() == pointer

  async def cleanup_blob():
    if blob_id is not None:
      async with graph_uow() as uow:
        await uow.storage.delete_blob(blob_id)

  try:
    async_runner.run(run())
  finally:
    _cleanup(persisted)
    async_runner.run(cleanup_blob())
