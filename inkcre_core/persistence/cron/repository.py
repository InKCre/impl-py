"""Locked Cron occurrences and database-clock observations."""

import datetime

import sqlmodel
from sqlalchemy.ext.asyncio import AsyncSession

from inkcre_core.schemas.cron import CronModel


class CronRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def get(self, cron_id: int, *, lock: bool = False, skip_locked: bool = False):
    statement = sqlmodel.select(CronModel).where(CronModel.id == cron_id)
    if lock:
      statement = statement.with_for_update(skip_locked=skip_locked)
    return (await self._session.scalars(statement)).one_or_none()

  async def database_now(self) -> datetime.datetime:
    return (
      await self._session.execute(sqlmodel.select(sqlmodel.func.statement_timestamp()))
    ).scalar_one()

  async def enabled_ids(self):
    return tuple(
      await self._session.scalars(
        sqlmodel.select(CronModel.id).where(sqlmodel.col(CronModel.enabled).is_(True))
      )
    )

  async def list(self, *, limit: int | None, cursor: int | None):
    statement = sqlmodel.select(CronModel).order_by(sqlmodel.col(CronModel.id))
    if cursor is not None:
      statement = statement.where(sqlmodel.col(CronModel.id) > cursor)
    if limit is not None:
      statement = statement.limit(limit + 1)
    rows = list((await self._session.scalars(statement)).all())
    more = limit is not None and len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].id if more else None

  async def save(self, cron: CronModel) -> None:
    self._session.add(cron)
    await self._session.flush()
    await self._session.refresh(cron)

  async def delete(self, cron: CronModel) -> None:
    await self._session.delete(cron)
