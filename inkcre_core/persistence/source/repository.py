"""Persistence for Source catalog and instances."""

import sqlmodel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from inkcre_core.schemas.source import SourceModel, SourceTypesModel
from inkcre_core.persistence.discovery import metadata_page


class SourceRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def sync_types(self, rows: list[dict]) -> None:
    if not rows:
      return
    statement = insert(SourceTypesModel).values(rows)
    statement = statement.on_conflict_do_update(
      index_elements=[SourceTypesModel.id],
      set_={
        key: getattr(statement.excluded, key)
        for key in (
          "description",
          "config_schema",
          "collect_config_schema",
          "backfill_config_schema",
        )
      },
    )
    await self._session.execute(statement)

  async def get(self, source_id: int, *, lock: bool = False):
    statement = sqlmodel.select(SourceModel).where(SourceModel.id == source_id)
    if lock:
      statement = statement.with_for_update()
    return (await self._session.scalars(statement)).one_or_none()

  async def get_by_block(self, block_id: int) -> SourceModel | None:
    return (
      await self._session.scalars(
        sqlmodel.select(SourceModel).where(SourceModel.block == block_id)
      )
    ).one_or_none()

  async def save(self, source: SourceModel) -> None:
    self._session.add(source)
    await self._session.flush()
    await self._session.refresh(source)

  async def get_type(self, type_: str) -> SourceTypesModel | None:
    return await self._session.get(SourceTypesModel, type_)

  async def list_sources(self, *, limit: int | None, cursor: int | None):
    statement = sqlmodel.select(SourceModel).order_by(sqlmodel.col(SourceModel.id))
    if cursor is not None:
      statement = statement.where(sqlmodel.col(SourceModel.id) > cursor)
    if limit is not None:
      statement = statement.limit(limit + 1)
    rows = list((await self._session.scalars(statement)).all())
    more = limit is not None and len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].id if more else None

  async def list_types(self, *, limit: int | None, cursor: str | None):
    statement = sqlmodel.select(SourceTypesModel).order_by(SourceTypesModel.id)
    if cursor is not None:
      statement = statement.where(SourceTypesModel.id > cursor)
    if limit is not None:
      statement = statement.limit(limit + 1)
    rows = list((await self._session.scalars(statement)).all())
    more = limit is not None and len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].id if more else None

  async def discover_types(self, *, limit: int, after: str | None) -> list[dict]:
    return await metadata_page(
      self._session,
      sqlmodel.select(
        SourceTypesModel.id,
        sqlmodel.func.substr(SourceTypesModel.description, 1, 512).label("description"),
      ),
      SourceTypesModel.id,
      limit=limit,
      after=after,
    )

  async def delete(self, source: SourceModel) -> None:
    await self._session.delete(source)
