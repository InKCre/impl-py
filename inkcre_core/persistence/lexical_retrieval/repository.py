"""Session-bound lexical ranking, candidate scans and projection batch writes."""

import datetime
import typing

import sqlalchemy
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
import sqlmodel

from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.lexical_retrieval import BlockLexicalRecordModel


class LexicalRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def search(self, normalized: str, limit: int):
    literal_pattern = f"%{self._escape_like(normalized)}%"

    block_columns = typing.cast(
      typing.Any,
      BlockModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    record_columns = typing.cast(
      typing.Any,
      BlockLexicalRecordModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    query_terms = sqlalchemy.func.plainto_tsquery("simple", normalized)
    label_exact = sqlalchemy.func.lower(record_columns.label) == normalized.lower()
    label_substring = record_columns.label.ilike(literal_pattern, escape="\\")
    text_substring = record_columns.text.ilike(literal_pattern, escape="\\")
    term_match = record_columns.search_vector.op("@@")(query_terms)
    evidence = sqlalchemy.case(
      (label_exact, "label_exact"),
      (label_substring, "label_substring"),
      (text_substring, "text_substring"),
      else_="terms",
    ).label("evidence")
    evidence_class = sqlalchemy.case(
      (label_exact, 4.0),
      (label_substring, 3.0),
      (text_substring, 2.0),
      else_=1.0,
    )
    term_rank = sqlalchemy.func.ts_rank_cd(record_columns.search_vector, query_terms)
    rank = (evidence_class + term_rank).label("rank")

    statement = (
      sqlmodel.select(BlockModel, BlockLexicalRecordModel, evidence, rank)
      .join(
        BlockLexicalRecordModel,
        record_columns.block == block_columns.id,
      )
      .where(
        record_columns.updated_at >= block_columns.updated_at,
        sqlalchemy.or_(
          label_exact,
          label_substring,
          text_substring,
          term_match,
        ),
      )
      .order_by(
        sqlalchemy.desc(evidence_class),
        sqlalchemy.desc(term_rank),
        block_columns.id,
      )
      .limit(limit)
    )
    return (await self._session.execute(statement)).all()

  async def candidate_page(
    self,
    cursor: int,
    page_size: int,
    rebuild_cutoff: datetime.datetime | None,
  ) -> tuple[tuple[BlockModel, ...], int, bool]:
    block_columns = typing.cast(
      typing.Any,
      BlockModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    record_columns = typing.cast(
      typing.Any,
      BlockLexicalRecordModel.__table__.c,  # pyrefly: ignore[missing-attribute]
    )
    needs_projection = sqlalchemy.or_(
      record_columns.block.is_(None),
      record_columns.updated_at < block_columns.updated_at,
    )
    if rebuild_cutoff is not None:
      needs_projection = sqlalchemy.or_(
        needs_projection,
        record_columns.updated_at < rebuild_cutoff,
      )
    statement = (
      sqlmodel.select(BlockModel)
      .outerjoin(
        BlockLexicalRecordModel,
        record_columns.block == block_columns.id,
      )
      .where(block_columns.id > cursor, needs_projection)
      .order_by(block_columns.id)
      .limit(page_size)
    )
    blocks = tuple(await self._session.scalars(statement))
    next_cursor = typing.cast(int, blocks[-1].id) if blocks else cursor
    return blocks, next_cursor, len(blocks) < page_size

  async def database_now(self) -> datetime.datetime:
    return (
      await self._session.execute(sqlmodel.select(sqlalchemy.func.current_timestamp()))
    ).scalar_one()

  async def upsert(self, projections: list[dict[str, typing.Any]]) -> None:
    if not projections:
      return
    rows = []
    for projection in projections:
      label_vector = sqlalchemy.func.setweight(
        sqlalchemy.func.to_tsvector("simple", projection["label"]),
        sqlalchemy.literal_column("'A'::\"char\""),
      )
      text_vector = sqlalchemy.func.setweight(
        sqlalchemy.func.to_tsvector("simple", projection["text"] or ""),
        sqlalchemy.literal_column("'D'::\"char\""),
      )
      rows.append({**projection, "search_vector": label_vector.op("||")(text_vector)})
    statement = insert(BlockLexicalRecordModel).values(rows)
    await self._session.execute(
      statement.on_conflict_do_update(
        index_elements=["block"],
        set_={
          "label": statement.excluded.label,
          "text": statement.excluded.text,
          "search_vector": statement.excluded.search_vector,
          "updated_at": sqlalchemy.func.current_timestamp(),
        },
      )
    )

  @staticmethod
  def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
