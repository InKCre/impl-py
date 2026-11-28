"""Session-bound access to canonical Extension rows and the enabled RPC."""

from __future__ import annotations

import uuid
import typing

import sqlalchemy
import sqlmodel
from sqlalchemy.ext.asyncio import AsyncSession

from inkcre_core.schemas.extension import ExtensionModel, ExtensionPeerParticipationModel


class ExtensionRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def list(self):
    rows = await self._session.scalars(
      sqlmodel.select(ExtensionModel).order_by(ExtensionModel.name)
    )
    return tuple(rows)

  async def page(
    self, limit: int, cursor: str | None
  ) -> tuple[typing.Sequence[ExtensionModel], str | None]:
    key = sqlmodel.col(ExtensionModel.name)
    statement = sqlmodel.select(ExtensionModel).order_by(key).limit(limit + 1)
    if cursor is not None:
      statement = statement.where(key > cursor)
    rows = list(await self._session.scalars(statement))
    return rows[:limit], rows[limit - 1].name if len(rows) > limit else None

  async def get(self, name: str, *, lock: bool = False):
    statement = sqlmodel.select(ExtensionModel).where(ExtensionModel.name == name)
    if lock:
      statement = statement.with_for_update()
    return (await self._session.scalars(statement)).one_or_none()

  async def try_install_lock(self, name: str) -> bool:
    result = await self._session.execute(
      sqlalchemy.text("SELECT pg_try_advisory_xact_lock(hashtextextended(:name, 0))"),
      {"name": name},
    )
    return bool(result.scalar_one())

  async def save(self, row: ExtensionModel) -> None:
    self._session.add(row)
    await self._session.flush()

  async def delete(self, row: ExtensionModel) -> None:
    await self._session.delete(row)

  async def list_participations(
    self, peer_id: uuid.UUID, names: tuple[str, ...] | None = None
  ):
    statement = (
      sqlmodel.select(ExtensionPeerParticipationModel)
      .where(ExtensionPeerParticipationModel.peer_id == peer_id)
      .order_by(ExtensionPeerParticipationModel.name)
    )
    if names is not None:
      statement = statement.where(
        sqlmodel.col(ExtensionPeerParticipationModel.name).in_(names)
      )
    return tuple(await self._session.scalars(statement))

  async def list_participations_for_extension(self, name: str):
    rows = await self._session.scalars(
      sqlmodel.select(ExtensionPeerParticipationModel).where(
        ExtensionPeerParticipationModel.name == name
      )
    )
    return tuple(rows)

  async def get_participation(self, name: str, peer_id: uuid.UUID, *, lock: bool = False):
    statement = sqlmodel.select(ExtensionPeerParticipationModel).where(
      ExtensionPeerParticipationModel.name == name,
      ExtensionPeerParticipationModel.peer_id == peer_id,
    )
    if lock:
      statement = statement.with_for_update()
    return (await self._session.scalars(statement)).one_or_none()

  async def save_participation(self, row: ExtensionPeerParticipationModel) -> None:
    self._session.add(row)
    await self._session.flush()

  async def delete_participation(self, row: ExtensionPeerParticipationModel) -> None:
    await self._session.delete(row)
