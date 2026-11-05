"""Deployment config persistence and transaction composition."""

from typing import Any

import sqlmodel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from inkcre_core.schemas.deployment_config import DeploymentConfigModel


class DeploymentConfigRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def get(
    self, key: str, *, for_update: bool = False
  ) -> DeploymentConfigModel | None:
    statement = sqlmodel.select(DeploymentConfigModel).where(
      DeploymentConfigModel.key == key
    )
    if for_update:
      statement = statement.with_for_update()
    return (await self._session.scalars(statement)).one_or_none()

  async def list(
    self, *, limit: int | None, cursor: str | None
  ) -> tuple[list[DeploymentConfigModel], str | None]:
    statement = sqlmodel.select(DeploymentConfigModel).order_by(DeploymentConfigModel.key)
    if cursor is not None:
      statement = statement.where(DeploymentConfigModel.key > cursor)
    if limit is not None:
      statement = statement.limit(limit + 1)
    rows = list((await self._session.scalars(statement)).all())
    more = limit is not None and len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].key if more else None

  async def replace(
    self, key: str, schema_id: str, value: dict[str, Any]
  ) -> tuple[DeploymentConfigModel, bool]:
    statement = insert(DeploymentConfigModel).values(
      key=key, schema_id=schema_id, value=value
    )
    statement = statement.on_conflict_do_nothing(
      index_elements=[DeploymentConfigModel.key]
    ).returning(DeploymentConfigModel)
    record = (await self._session.scalars(statement)).one_or_none()
    if record is not None:
      return record, True
    # The insert conflict, rather than a preceding read, determines creation status.
    statement = (
      sqlmodel.update(DeploymentConfigModel)
      .where(sqlmodel.col(DeploymentConfigModel.key) == key)
      .values(schema_id=schema_id, value=value)
      .returning(DeploymentConfigModel)
    )
    record = (await self._session.scalars(statement)).one()
    return record, False

  async def save(self, record: DeploymentConfigModel) -> None:
    self._session.add(record)
    await self._session.flush()
    # UPDATE timestamps are managed by database triggers, not Python defaults.
    await self._session.refresh(record)

  async def delete(self, record: DeploymentConfigModel) -> None:
    await self._session.delete(record)
