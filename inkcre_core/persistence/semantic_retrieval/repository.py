"""Session-bound embedding profiles and derived retrieval records."""

import datetime
import typing

import sqlalchemy
import sqlalchemy.orm
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
import sqlmodel

from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.semantic_retrieval import VectorRetrievalOptions

from inkcre_core.schemas.ai import (
  EmbeddingProfileID,
  EmbeddingProfileModel,
  BlockEmbeddingModel,
  RelationEmbeddingModel,
)


class SemanticRetrievalRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def get_profile(
    self, profile_id: EmbeddingProfileID
  ) -> EmbeddingProfileModel | None:
    return await self._session.get(EmbeddingProfileModel, profile_id)

  async def list_profiles(self, *, limit: int | None, cursor: int | None):
    statement = sqlmodel.select(EmbeddingProfileModel).order_by(
      sqlmodel.col(EmbeddingProfileModel.id)
    )
    if cursor is not None:
      statement = statement.where(sqlmodel.col(EmbeddingProfileModel.id) > cursor)
    if limit is not None:
      statement = statement.limit(limit + 1)
    rows = list(await self._session.scalars(statement))
    more = limit is not None and len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].id if more else None

  async def database_now(self) -> datetime.datetime:
    return (
      await self._session.execute(sqlmodel.select(sqlalchemy.func.current_timestamp()))
    ).scalar_one()

  async def candidate_blocks(self, profile_id: int, cursor: int, page_size: int):
    block_columns = typing.cast(
      typing.Any,
      BlockModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    record_columns = typing.cast(
      typing.Any,
      BlockEmbeddingModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    statement = (
      sqlmodel.select(BlockModel, BlockEmbeddingModel)
      .outerjoin(
        BlockEmbeddingModel,
        sqlalchemy.and_(
          record_columns.profile == profile_id,
          record_columns.block == block_columns.id,
        ),
      )
      .where(block_columns.id > cursor)
      .order_by(block_columns.id)
      .limit(page_size)
    )
    return (await self._session.execute(statement)).all()

  async def candidate_relations(self, profile_id: int, cursor: int, page_size: int):
    from_block = sqlalchemy.orm.aliased(BlockModel)
    to_block = sqlalchemy.orm.aliased(BlockModel)
    from_columns = typing.cast(typing.Any, from_block)
    to_columns = typing.cast(typing.Any, to_block)
    relation_columns = typing.cast(
      typing.Any,
      RelationModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    record_columns = typing.cast(
      typing.Any,
      RelationEmbeddingModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    statement = (
      sqlmodel.select(
        RelationModel,
        RelationEmbeddingModel,
        from_block.updated_at,
        to_block.updated_at,
      )
      .outerjoin(
        RelationEmbeddingModel,
        sqlalchemy.and_(
          record_columns.profile == profile_id,
          record_columns.relation == relation_columns.id,
        ),
      )
      .join(from_block, from_columns.id == relation_columns.from_)
      .join(to_block, to_columns.id == relation_columns.to_)
      .where(relation_columns.id > cursor)
      .order_by(relation_columns.id)
      .limit(page_size)
    )
    return (await self._session.execute(statement)).all()

  async def retrieve_blocks(
    self,
    profile: EmbeddingProfileModel,
    query_vector,
    options: VectorRetrievalOptions,
  ):
    profile_id = profile.id
    block_columns = typing.cast(
      typing.Any,
      BlockModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    record_columns = typing.cast(
      typing.Any,
      BlockEmbeddingModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    distance = record_columns.embedding.cosine_distance(query_vector).label("distance")
    statement = (
      sqlmodel.select(BlockModel, distance)
      .join(BlockEmbeddingModel, record_columns.block == block_columns.id)
      .where(
        record_columns.profile == profile_id,
        record_columns.updated_at >= profile.updated_at,
        record_columns.updated_at >= block_columns.updated_at,
        sqlalchemy.func.vector_dims(record_columns.embedding) == profile.dimensions,
        sqlalchemy.func.vector_norm(record_columns.embedding) > 0,
      )
      .order_by(distance, block_columns.id)
      .limit(options.limit)
    )
    if options.min_score is not None:
      statement = statement.where(distance <= 1 - options.min_score)
    return (await self._session.execute(statement)).all()

  async def retrieve_relations(
    self,
    profile: EmbeddingProfileModel,
    query_vector,
    options: VectorRetrievalOptions,
  ):
    profile_id = profile.id
    from_block = sqlalchemy.orm.aliased(BlockModel)
    to_block = sqlalchemy.orm.aliased(BlockModel)
    from_columns = typing.cast(typing.Any, from_block)
    to_columns = typing.cast(typing.Any, to_block)
    relation_columns = typing.cast(
      typing.Any,
      RelationModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    record_columns = typing.cast(
      typing.Any,
      RelationEmbeddingModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    distance = record_columns.embedding.cosine_distance(query_vector).label("distance")
    statement = (
      sqlmodel.select(RelationModel, distance)
      .join(
        RelationEmbeddingModel,
        record_columns.relation == relation_columns.id,
      )
      .join(from_block, from_columns.id == relation_columns.from_)
      .join(to_block, to_columns.id == relation_columns.to_)
      .where(
        record_columns.profile == profile_id,
        record_columns.updated_at >= profile.updated_at,
        record_columns.updated_at >= relation_columns.updated_at,
        record_columns.updated_at >= from_columns.updated_at,
        record_columns.updated_at >= to_columns.updated_at,
        sqlalchemy.func.vector_dims(record_columns.embedding) == profile.dimensions,
        sqlalchemy.func.vector_norm(record_columns.embedding) > 0,
      )
      .order_by(distance, relation_columns.id)
      .limit(options.limit)
    )
    if options.min_score is not None:
      statement = statement.where(distance <= 1 - options.min_score)
    return (await self._session.execute(statement)).all()

  async def upsert(self, blocks: list[dict], relations: list[dict]) -> None:
    for model, key, rows in (
      (BlockEmbeddingModel, "block", blocks),
      (RelationEmbeddingModel, "relation", relations),
    ):
      if not rows:
        continue
      statement = insert(model).values(rows)
      await self._session.execute(
        statement.on_conflict_do_update(
          index_elements=["profile", key],
          set_={
            "embedding": statement.excluded.embedding,
            "updated_at": sqlalchemy.func.current_timestamp(),
          },
        )
      )
