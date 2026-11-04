"""Durable background-command schemas."""

import datetime
import enum
import typing

import pydantic
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel


JobTypeID: typing.TypeAlias = str
JobID: typing.TypeAlias = int


class JobStatus(enum.StrEnum):
  PENDING = "pending"
  RUNNING = "running"
  FINISHED = "finished"
  FAILED = "failed"
  TIMED_OUT = "timed_out"
  ABORTED = "aborted"

  @property
  def terminal(self) -> bool:
    return self not in {self.PENDING, self.RUNNING}


class JobTypeModel(sqlmodel.SQLModel, table=True):
  """Database projection of one exact runtime Job Handler contract."""

  __tablename__ = "job_types"  # type: ignore
  __table_args__ = (
    sqlalchemy.CheckConstraint(
      "default_timeout_seconds > 0",
      name="job_types_default_timeout_seconds_positive",
    ),
  )

  id: JobTypeID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
  )
  description: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  parameters_schema: dict[str, typing.Any] = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'{}'::jsonb"),
    ),
  )
  default_timeout_seconds: int = sqlmodel.Field(
    gt=0,
    sa_column=sqlalchemy.Column(sqlalchemy.Integer, nullable=False),
  )


class JobModel(sqlmodel.SQLModel, table=True):
  """One durable execution opportunity for an exact registered command."""

  __tablename__ = "jobs"  # type: ignore
  __table_args__ = (
    sqlalchemy.Index("jobs_status_idx", "status"),
    sqlalchemy.CheckConstraint(
      "octet_length(submission_traceparent) <= 512",
      name="jobs_submission_traceparent_capacity",
    ),
    sqlalchemy.CheckConstraint(
      "octet_length(submission_tracestate) <= 512",
      name="jobs_submission_tracestate_capacity",
    ),
    sqlalchemy.CheckConstraint(
      "timeout_seconds > 0",
      name="jobs_timeout_seconds_positive",
    ),
    sqlalchemy.CheckConstraint(
      "(status = 'pending' AND started_at IS NULL AND closed_at IS NULL) OR "
      "(status = 'running' AND started_at IS NOT NULL AND closed_at IS NULL) OR "
      "(status IN ('finished', 'failed', 'timed_out', 'aborted') "
      "AND closed_at IS NOT NULL)",
      name="jobs_lifecycle_timestamps_valid",
    ),
  )

  id: JobID | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.Identity(),
      primary_key=True,
    ),
  )
  type: JobTypeID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Text,
      sqlalchemy.ForeignKey("job_types.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=False,
    )
  )
  parameters: dict[str, typing.Any] = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'{}'::jsonb"),
    ),
  )
  submission_traceparent: str | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  submission_tracestate: str | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  state: dict[str, typing.Any] = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'{}'::jsonb"),
    ),
  )
  abort_requested: bool = sqlmodel.Field(
    default=False,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Boolean, nullable=False, server_default=sqlalchemy.false()
    ),
  )
  timeout_seconds: int = sqlmodel.Field(
    gt=0,
    sa_column=sqlalchemy.Column(sqlalchemy.Integer, nullable=False),
  )
  status: JobStatus = sqlmodel.Field(
    default=JobStatus.PENDING,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Enum(
        JobStatus,
        name="jobstatus",
        values_callable=lambda values: [value.value for value in values],
        inherit_schema=True,
      ),
      nullable=False,
      server_default=JobStatus.PENDING.value,
    ),
  )
  created_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )
  started_at: datetime.datetime | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.TIMESTAMP(timezone=True), nullable=True),
  )
  closed_at: datetime.datetime | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.TIMESTAMP(timezone=True), nullable=True),
  )


class JobCreateForm(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  type: JobTypeID
  parameters: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
  timeout_seconds: int | None = pydantic.Field(default=None, gt=0)
