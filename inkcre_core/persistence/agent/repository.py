"""Persisted Agent definitions; Thread runtime state has a separate owner."""

import sqlmodel
from sqlalchemy.ext.asyncio import AsyncSession
from inkcre_core.schemas.agent import AgentDefinitionModel, AgentForm


class AgentRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def get(
    self, agent_id: int, *, for_update: bool = False
  ) -> AgentDefinitionModel | None:
    statement = sqlmodel.select(AgentDefinitionModel).where(
      AgentDefinitionModel.id == agent_id
    )
    if for_update:
      statement = statement.with_for_update()
    return (await self._session.scalars(statement)).one_or_none()

  async def list(
    self, *, limit: int | None, cursor: int | None
  ) -> tuple[list[AgentDefinitionModel], int | None]:
    statement = sqlmodel.select(AgentDefinitionModel).order_by(
      sqlmodel.col(AgentDefinitionModel.id)
    )
    if cursor is not None:
      statement = statement.where(sqlmodel.col(AgentDefinitionModel.id) > cursor)
    if limit is not None:
      statement = statement.limit(limit + 1)
    rows = list((await self._session.scalars(statement)).all())
    more = limit is not None and len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].id if more else None

  async def create(self, form: AgentForm) -> AgentDefinitionModel:
    record = AgentDefinitionModel(**form.model_dump())
    self._session.add(record)
    await self._session.flush()
    return record

  async def save(self, record: AgentDefinitionModel) -> None:
    self._session.add(record)
    await self._session.flush()
    await self._session.refresh(record)

  async def delete(self, record: AgentDefinitionModel) -> None:
    await self._session.delete(record)
