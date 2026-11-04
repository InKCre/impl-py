"""Deployment-wide recurring Job-template schemas."""

import datetime
import typing

import pydantic
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel

from .job import JobID, JobTypeID


CronID: typing.TypeAlias = int


class CronModel(sqlmodel.SQLModel, table=True):
  """One five-field UNIX schedule that materializes typed Jobs."""

  __tablename__ = "crons"  # type: ignore
  __table_args__ = (
    sqlalchemy.Index("crons_enabled_idx", "enabled"),
    sqlalchemy.CheckConstraint(
      "job_timeout_seconds IS NULL OR job_timeout_seconds > 0",
      name="crons_job_timeout_seconds_positive",
    ),
  )

  id: CronID | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.Identity(),
      primary_key=True,
    ),
  )
  schedule: str = sqlmodel.Field(
    min_length=1,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False),
  )
  enabled: bool = sqlmodel.Field(
    default=True,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Boolean,
      nullable=False,
      server_default=sqlalchemy.true(),
    ),
  )
  job_type: JobTypeID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Text,
      sqlalchemy.ForeignKey("job_types.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=False,
    )
  )
  job_parameters: dict[str, typing.Any] = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'{}'::jsonb"),
    ),
  )
  job_timeout_seconds: int | None = sqlmodel.Field(
    default=None,
    gt=0,
    sa_column=sqlalchemy.Column(sqlalchemy.Integer, nullable=True),
  )
  last_job: JobID | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.ForeignKey("jobs.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=True,
    ),
  )
  last_scheduled_for: datetime.datetime | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.TIMESTAMP(timezone=True), nullable=True),
  )
  created_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )
  updated_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )


class CronForm(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  schedule: str = pydantic.Field(min_length=1)
  enabled: bool = True
  job_type: JobTypeID
  job_parameters: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
  job_timeout_seconds: int | None = pydantic.Field(default=None, gt=0)


class CronUpdateForm(pydantic.BaseModel):
  """Optional submitted fields; null only clears the timeout override."""

  model_config = pydantic.ConfigDict(extra="forbid")

  # Omission is not null input: exclude_unset removes these defaults, whereas
  # an explicitly submitted null still fails the non-null field type.
  schedule: str = pydantic.Field(default=None, min_length=1)  # pyrefly: ignore[bad-assignment]
  enabled: bool = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]
  job_type: JobTypeID = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]
  job_parameters: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
  job_timeout_seconds: int | None = pydantic.Field(default=None, gt=0)
