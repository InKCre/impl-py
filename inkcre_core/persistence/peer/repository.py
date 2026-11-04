"""Peer persistence; all lease comparisons use the database clock."""

import datetime
from typing import Any

import sqlalchemy
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
import sqlmodel

from inkcre_core.database_contract import PROTOCOL_SCHEMA
from inkcre_core.schemas.peer import PeerModel, PeerRef


class PeerRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def register(
    self,
    peer_id: PeerRef,
    name: str,
    application_version: str,
    schema: dict,
  ) -> PeerModel:
    statement = insert(PeerModel).values(
      id=peer_id,
      name=name,
      application_version=application_version,
      labels=[],
      config={},
      config_schema=schema,
      capabilities=[],
    )
    statement = statement.on_conflict_do_update(
      index_elements=["id"],
      set_={
        "application_version": statement.excluded.application_version,
        "config_schema": statement.excluded.config_schema,
      },
    ).returning(PeerModel)
    return (await self._session.scalars(statement)).one()

  async def get(self, peer_id: PeerRef, *, for_update: bool = False) -> PeerModel | None:
    statement = sqlmodel.select(PeerModel).where(PeerModel.id == peer_id)
    if for_update:
      statement = statement.with_for_update()
    return (await self._session.scalars(statement)).one_or_none()

  async def get_all(self) -> tuple[PeerModel, ...]:
    return tuple((await self._session.scalars(sqlmodel.select(PeerModel))).all())

  async def with_leases(
    self,
    *,
    peer_id: PeerRef | None = None,
    limit: int | None = None,
    cursor: PeerRef | None = None,
  ) -> tuple[list[dict[str, Any]], PeerRef | None]:
    statement = sqlmodel.select(
      PeerModel,
      sqlalchemy.func.coalesce(
        PeerModel.lease_expires_at > sqlalchemy.func.statement_timestamp(), False
      ),
    ).order_by(sqlmodel.col(PeerModel.id))
    if peer_id is not None:
      statement = statement.where(PeerModel.id == peer_id)
    if cursor is not None:
      statement = statement.where(PeerModel.id > cursor)
    if limit is not None:
      statement = statement.limit(limit + 1)
    rows = list((await self._session.execute(statement)).all())
    more = limit is not None and len(rows) > limit
    rows = rows[:limit]
    return [
      {**row.model_dump(mode="json"), "lease_active": active} for row, active in rows
    ], rows[-1][0].id if more else None

  async def save(self, peer: PeerModel) -> None:
    self._session.add(peer)
    await self._session.flush()
    await self._session.refresh(peer)

  async def renew_lease(self, peer_id: PeerRef, ttl_seconds: int) -> datetime.datetime:
    statement = sqlalchemy.select(
      getattr(sqlalchemy.func, PROTOCOL_SCHEMA).renew_peer_lease(peer_id, ttl_seconds)
    )
    return (await self._session.execute(statement)).scalar_one()

  async def clear_lease(self, peer_id: PeerRef) -> None:
    await self._session.execute(
      sqlalchemy.update(PeerModel)
      .where(sqlmodel.col(PeerModel.id) == peer_id)
      .values(lease_expires_at=None)
    )

  async def candidates(
    self, self_id: PeerRef, route_to_peer: PeerRef | None
  ) -> tuple[PeerModel, ...]:
    statement = sqlmodel.select(PeerModel).where(
      PeerModel.id != self_id,
      PeerModel.lease_expires_at > sqlalchemy.func.statement_timestamp(),
    )
    if route_to_peer is not None:
      statement = statement.where(PeerModel.id == route_to_peer)
    return tuple((await self._session.scalars(statement)).all())
