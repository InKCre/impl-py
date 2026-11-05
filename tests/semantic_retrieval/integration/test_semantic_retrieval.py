"""Real PostgreSQL vertical proof for maintenance, freshness and ranking."""

from inkcre_core.business.deployment_config import DeploymentConfigService
from inkcre_core.business.info_base.services import RelationService


import os
import time

import pytest
import sqlalchemy

from inkcre_core.business.ai import AIManager
from inkcre_core.business.info_base.resolver import register_core_resolvers
from inkcre_core.business.semantic_retrieval import (
  EmbeddingProfileNotFoundError,
  SEMANTIC_RETRIEVAL_CONFIG_KEY,
  SEMANTIC_RETRIEVAL_CONFIG_SCHEMA,
  SemanticRetrievalManager,
)
from tests.database import TestSession
from inkcre_core.schemas.ai import (
  AIModelModel,
  AIProviderModel,
  BlockEmbeddingModel,
  EmbeddingCapability,
  EmbeddingProfileModel,
)
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.semantic_retrieval import (
  EmbeddingMaintenanceOptions,
  VectorRetrievalOptions,
)


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)

ALPHA = "semantic-alpha-019fad3c"
BETA = "semantic-beta-019fad3c"
DISTRACTOR = "semantic-distractor-019fad3c"


def _vector(text: str) -> tuple[float, float, float]:
  if "property:\nowns" in text:
    return (0.95, 0.3122498999, 0.0)
  if ALPHA in text:
    return (1.0, 0.0, 0.0)
  if BETA in text:
    return (0.6, 0.8, 0.0)
  return (0.0, 0.0, 1.0)


async def _cleanup(
  provider_id: int | None,
  profile_id: int | None,
  block_ids: tuple[int, ...],
  previous_config,
) -> None:
  with TestSession() as db:
    if block_ids:
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.blocks WHERE id = ANY(:ids)"),
        {"ids": list(block_ids)},
      )
    if profile_id is not None:
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.block_embeddings WHERE profile = :profile"),
        {"profile": profile_id},
      )
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.relation_embeddings WHERE profile = :profile"),
        {"profile": profile_id},
      )
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.embedding_profiles WHERE id = :profile"),
        {"profile": profile_id},
      )
    if provider_id is not None:
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.ai_models WHERE provider = :provider"),
        {"provider": provider_id},
      )
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.ai_providers WHERE id = :provider"),
        {"provider": provider_id},
      )
    db.connection().execute(
      sqlalchemy.text("DELETE FROM inkcre.configs WHERE key = :key"),
      {"key": SEMANTIC_RETRIEVAL_CONFIG_KEY},
    )
    db.commit()
  if previous_config is not None:
    await DeploymentConfigService.replace(
      previous_config.key, previous_config.schema_id, previous_config.value
    )


def test_real_maintenance_freshness_and_global_retrieval(async_runner, monkeypatch):
  register_core_resolvers()
  async_runner.run(AIManager.sync_dialects_async())
  previous_config = async_runner.run(
    DeploymentConfigService.read(SEMANTIC_RETRIEVAL_CONFIG_KEY)
  )
  provider_id: int | None = None
  profile_id: int | None = None
  block_ids: list[int] = []
  try:
    with TestSession() as db:
      provider = AIProviderModel(
        name="Semantic retrieval integration provider",
        dialect="core.openai-compatible.v1",
        config={"api_key": "unused"},
      )
      db.add(provider)
      db.flush()
      provider_id = provider.id
      assert provider_id is not None
      model = AIModelModel(
        provider=provider_id,
        native_model_id="semantic-retrieval-integration-model",
        capabilities=(
          EmbeddingCapability(
            input_modalities=["text"],
            output_modalities=["vector"],
          ),
        ),
      )
      db.add(model)
      db.flush()
      assert model.id is not None
      profile = EmbeddingProfileModel(
        name="Semantic retrieval integration profile",
        ai_model=model.id,
        dimensions=3,
      )
      db.add(profile)
      db.flush()
      profile_id = profile.id
      assert profile_id is not None

      unavailable = BlockModel(resolver="core.image.v1", content="not-read")
      alpha = BlockModel(resolver="core.text.v1", content=f"{ALPHA} systems")
      beta = BlockModel(resolver="core.text.v1", content=f"{BETA} knowledge")
      distractor = BlockModel(
        resolver="core.text.v1",
        content=f"{DISTRACTOR} unrelated",
      )
      db.add_all((unavailable, alpha, beta, distractor))
      db.flush()
      assert unavailable.id is not None
      assert alpha.id is not None
      assert beta.id is not None
      assert distractor.id is not None
      block_ids.extend((unavailable.id, alpha.id, beta.id, distractor.id))
      relation = RelationModel(from_=alpha.id, to_=beta.id, content="owns")
      db.add(relation)
      db.commit()
      db.refresh(relation)
      assert relation.id is not None
      alpha_id = alpha.id
      beta_id = beta.id
      relation_id = relation.id

    async_runner.run(
      DeploymentConfigService.replace(
        SEMANTIC_RETRIEVAL_CONFIG_KEY,
        SEMANTIC_RETRIEVAL_CONFIG_SCHEMA,
        {"default_profile": profile_id},
      )
    )

    async def embed(_cls, model, inputs, dimensions):
      assert dimensions == 3
      return tuple(_vector(text) for text in inputs)

    monkeypatch.setattr(AIManager, "embed", classmethod(embed))
    maintenance = async_runner.run(
      SemanticRetrievalManager.maintain(
        options=EmbeddingMaintenanceOptions(
          max_embeddings=10_000,
          batch_size=4,
          scan_page_size=1,
        )
      )
    )
    assert maintenance.unavailable >= 1
    assert maintenance.embedded >= 4

    result = async_runner.run(
      SemanticRetrievalManager.retrieve(
        f"find {ALPHA}",
        options=VectorRetrievalOptions(limit=3),
      )
    )
    assert [(match.type, match.entity.id) for match in result.matches] == [
      ("block", alpha_id),
      ("relation", relation_id),
      ("block", beta_id),
    ]
    assert result.matches[0].score > result.matches[1].score > result.matches[2].score

    blocks_only = async_runner.run(
      SemanticRetrievalManager.retrieve(
        f"find {ALPHA}",
        profile_id,
        VectorRetrievalOptions(
          limit=5,
          min_score=0.5,
          entity_types={"block"},
        ),
      )
    )
    assert [(match.type, match.entity.id) for match in blocks_only.matches] == [
      ("block", alpha_id),
      ("block", beta_id),
    ]

    relation_text = async_runner.run(
      RelationService.get_text(
        RelationModel(
          id=relation_id,
          from_=alpha_id,
          to_=beta_id,
          content="owns",
        )
      )
    )
    assert relation_text == (
      f"subject:\ntext <{ALPHA} systems>\nproperty:\nowns\nvalue:\ntext <{BETA} knowledge>"
    )

    time.sleep(0.002)
    with TestSession() as db:
      alpha_row = db.get(BlockModel, alpha_id)
      assert alpha_row is not None
      alpha_row.content = f"{ALPHA} systems updated"
      db.add(alpha_row)
      db.commit()

    stale = async_runner.run(SemanticRetrievalManager.retrieve(f"find {ALPHA}", profile_id))
    stale_identities = {(match.type, match.entity.id) for match in stale.matches}
    assert ("block", alpha_id) not in stale_identities
    assert ("relation", relation_id) not in stale_identities
    assert ("block", beta_id) in stale_identities

    refreshed = async_runner.run(
      SemanticRetrievalManager.maintain(
        profile_id,
        EmbeddingMaintenanceOptions(max_embeddings=10, batch_size=2),
      )
    )
    assert refreshed.embedded == 2

    with TestSession() as db:
      db.connection().execute(
        sqlalchemy.text(
          "UPDATE inkcre.block_embeddings "
          "SET embedding = '[0.6,0.8]'::vector, updated_at = statement_timestamp() "
          "WHERE profile = :profile AND block = :block"
        ),
        {"profile": profile_id, "block": beta_id},
      )
      db.commit()
    mixed_dimensions = async_runner.run(
      SemanticRetrievalManager.retrieve(f"find {ALPHA}", profile_id)
    )
    assert ("block", beta_id) not in {
      (match.type, match.entity.id) for match in mixed_dimensions.matches
    }
    dimension_repair = async_runner.run(
      SemanticRetrievalManager.maintain(
        profile_id,
        EmbeddingMaintenanceOptions(max_embeddings=10, batch_size=2),
      )
    )
    assert dimension_repair.embedded == 1

    time.sleep(0.002)
    with TestSession() as db:
      profile_row = db.get(EmbeddingProfileModel, profile_id)
      assert profile_row is not None
      profile_row.name = "Updated semantic retrieval integration profile"
      db.add(profile_row)
      db.commit()
    invalidated = async_runner.run(
      SemanticRetrievalManager.retrieve(f"find {ALPHA}", profile_id)
    )
    assert invalidated.matches == ()
    rebuilt = async_runner.run(
      SemanticRetrievalManager.rebuild(
        profile_id,
        EmbeddingMaintenanceOptions(max_embeddings=10_000, batch_size=4),
      )
    )
    assert rebuilt.embedded >= 4

    with TestSession() as db:
      failure_block = BlockModel(
        resolver="core.text.v1",
        content="semantic-provider-failure-019fad3c",
      )
      db.add(failure_block)
      db.commit()
      db.refresh(failure_block)
      assert failure_block.id is not None
      failure_block_id = failure_block.id
      block_ids.append(failure_block_id)

    async def provider_failure(_cls, model, inputs, dimensions):
      raise RuntimeError("provider unavailable")

    monkeypatch.setattr(AIManager, "embed", classmethod(provider_failure))
    failed = async_runner.run(
      SemanticRetrievalManager.maintain(
        profile_id,
        EmbeddingMaintenanceOptions(max_embeddings=1, batch_size=1),
      )
    )
    assert failed.failed == 1
    with TestSession() as db:
      assert db.get(BlockEmbeddingModel, (profile_id, failure_block_id)) is None

    with TestSession() as db:
      second_failure_block = BlockModel(
        resolver="core.text.v1",
        content="semantic-cardinality-failure-019fad3c",
      )
      db.add(second_failure_block)
      db.commit()
      db.refresh(second_failure_block)
      assert second_failure_block.id is not None
      second_failure_block_id = second_failure_block.id
      block_ids.append(second_failure_block_id)

    async def wrong_cardinality(_cls, model, inputs, dimensions):
      assert len(inputs) == 2
      return ((0.0, 0.0, 1.0),)

    monkeypatch.setattr(AIManager, "embed", classmethod(wrong_cardinality))
    invalid_batch = async_runner.run(
      SemanticRetrievalManager.maintain(
        profile_id,
        EmbeddingMaintenanceOptions(max_embeddings=2, batch_size=2),
      )
    )
    assert invalid_batch.failed == 2
    with TestSession() as db:
      assert db.get(BlockEmbeddingModel, (profile_id, failure_block_id)) is None
      assert db.get(BlockEmbeddingModel, (profile_id, second_failure_block_id)) is None

    monkeypatch.setattr(AIManager, "embed", classmethod(embed))
    first_resumed_batch = async_runner.run(
      SemanticRetrievalManager.maintain(
        profile_id,
        EmbeddingMaintenanceOptions(max_embeddings=1, batch_size=1),
      )
    )
    assert first_resumed_batch.embedded == 1
    second_resumed_batch = async_runner.run(
      SemanticRetrievalManager.maintain(
        profile_id,
        EmbeddingMaintenanceOptions(max_embeddings=10, batch_size=2),
      )
    )
    assert second_resumed_batch.embedded == 1

    async_runner.run(
      DeploymentConfigService.replace(
        SEMANTIC_RETRIEVAL_CONFIG_KEY,
        SEMANTIC_RETRIEVAL_CONFIG_SCHEMA,
        {"default_profile": 9_223_372_036_854_775_000},
      )
    )
    with pytest.raises(EmbeddingProfileNotFoundError):
      async_runner.run(SemanticRetrievalManager.retrieve("dangling default"))
  finally:
    async_runner.run(_cleanup(provider_id, profile_id, tuple(block_ids), previous_config))
