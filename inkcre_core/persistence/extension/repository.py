"""Session-bound access to canonical Extension rows and the enabled RPC."""

import uuid

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

  async def list_participations(self, peer_id: uuid.UUID):
    rows = await self._session.scalars(
      sqlmodel.select(ExtensionPeerParticipationModel)
      .where(ExtensionPeerParticipationModel.peer_id == peer_id)
      .order_by(ExtensionPeerParticipationModel.name)
    )
    return tuple(rows)

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
