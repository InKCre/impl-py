"""Session-bound Sink catalog and instance records."""

import sqlmodel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from inkcre_core.schemas.sink import SinkModel, SinkTypeModel


class SinkRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def sync_types(self, rows: list[dict]) -> None:
    if not rows:
      return
    statement = insert(SinkTypeModel).values(rows)
    await self._session.execute(
      statement.on_conflict_do_update(
        index_elements=["id"],
        set_={
          "description": statement.excluded.description,
          "config_schema": statement.excluded.config_schema,
        },
      )
    )

  async def list_types(self):
    return tuple(
      await self._session.scalars(sqlmodel.select(SinkTypeModel).order_by(SinkTypeModel.id))
    )

  async def get_type(self, sink_type: str) -> SinkTypeModel | None:
    return await self._session.get(SinkTypeModel, sink_type)

  async def list(self):
    return tuple(
      await self._session.scalars(
        sqlmodel.select(SinkModel).order_by(sqlmodel.col(SinkModel.id))
      )
    )

  async def get(self, sink_id: int, *, lock: bool = False) -> SinkModel | None:
    statement = sqlmodel.select(SinkModel).where(SinkModel.id == sink_id)
    if lock:
      statement = statement.with_for_update()
    return (await self._session.scalars(statement)).one_or_none()

  async def save(self, sink: SinkModel) -> None:
    self._session.add(sink)
    await self._session.flush()
    await self._session.refresh(sink)

  async def delete(self, sink: SinkModel) -> None:
    await self._session.delete(sink)
