"""Session-bound durable Job catalog, claim and terminal writes."""

import typing

import sqlalchemy
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
import sqlmodel

from inkcre_core.schemas.job import JobModel, JobStatus, JobTypeModel


class JobRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def sync_types(self, rows: list[dict]) -> None:
    if not rows:
      return
    statement = insert(JobTypeModel).values(rows)
    await self._session.execute(
      statement.on_conflict_do_update(
        index_elements=[JobTypeModel.id],
        set_={
          key: getattr(statement.excluded, key)
          for key in ("description", "parameters_schema", "default_timeout_seconds")
        },
      )
    )

  async def get(self, job_id: int) -> JobModel | None:
    return await self._session.get(JobModel, job_id)

  async def get_type(self, type_: str) -> JobTypeModel | None:
    return await self._session.get(JobTypeModel, type_)

  async def create(self, job: JobModel) -> JobModel:
    self._session.add(job)
    await self._session.flush()
    await self._session.refresh(job)
    return job

  async def list_types(self, *, limit: int | None, cursor: str | None):
    statement = sqlmodel.select(JobTypeModel).order_by(JobTypeModel.id)
    if cursor is not None:
      statement = statement.where(JobTypeModel.id > cursor)
    if limit is not None:
      statement = statement.limit(limit + 1)
    rows = list((await self._session.scalars(statement)).all())
    more = limit is not None and len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].id if more else None

  async def list_jobs(
    self, *, limit: int, cursor: int | None, type_: str | None, status: JobStatus | None
  ):
    statement = sqlmodel.select(JobModel).order_by(sqlmodel.col(JobModel.id).desc())
    if cursor is not None:
      statement = statement.where(sqlmodel.col(JobModel.id) < cursor)
    if type_ is not None:
      statement = statement.where(JobModel.type == type_)
    if status is not None:
      statement = statement.where(JobModel.status == status)
    rows = list((await self._session.scalars(statement.limit(limit + 1))).all())
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].id if more else None

  async def abort(self, job_id: int) -> JobModel | None:
    table = typing.cast(typing.Any, getattr(JobModel, "__table__"))
    await self._session.execute(
      sqlalchemy.update(table)
      .where(
        table.c.id == job_id,
        table.c.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
      )
      .values(
        abort_requested=True,
        status=sqlalchemy.case(
          (
            table.c.status == JobStatus.PENDING,
            sqlalchemy.cast(JobStatus.ABORTED.value, table.c.status.type),
          ),
          else_=table.c.status,
        ),
      )
    )
    return await self.get(job_id)

  async def abort_requests(self, active_ids: tuple[int, ...]):
    return tuple(
      await self._session.scalars(
        sqlmodel.select(JobModel.id).where(
          sqlmodel.col(JobModel.id).in_(active_ids),
          sqlmodel.col(JobModel.abort_requested).is_(True),
        )
      )
    )

  async def claim(self, job_id: int) -> JobModel | None:
    result = await self._session.scalars(
      sqlalchemy.update(JobModel)
      .where(
        sqlmodel.col(JobModel.id) == job_id,
        sqlmodel.col(JobModel.status) == JobStatus.PENDING,
      )
      .values(status=JobStatus.RUNNING)
      .returning(JobModel)
    )
    return result.one_or_none()

  async def close(self, job: JobModel, status: JobStatus) -> bool:
    table = typing.cast(typing.Any, getattr(JobModel, "__table__"))
    result = await self._session.execute(
      sqlalchemy.update(table)
      .where(table.c.id == job.id, table.c.status == JobStatus.RUNNING)
      .values(status=status, state=job.state)
    )
    return bool(typing.cast(typing.Any, result).rowcount)

  async def expire_overdue(self) -> int:
    table = typing.cast(typing.Any, getattr(JobModel, "__table__"))
    result = await self._session.execute(
      sqlalchemy.update(table)
      .where(
        table.c.status == JobStatus.RUNNING,
        sqlalchemy.text(
          "started_at + timeout_seconds * interval '1 second' <= statement_timestamp()"
        ),
      )
      .values(status=JobStatus.TIMED_OUT)
    )
    return typing.cast(typing.Any, result).rowcount or 0

  async def pending(self):
    return tuple(
      await self._session.scalars(
        sqlmodel.select(JobModel).where(JobModel.status == JobStatus.PENDING)
      )
    )
