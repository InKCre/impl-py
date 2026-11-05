"""Persisted Sink catalog and instance contracts."""

import typing
import uuid

import pydantic
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel


SinkTypeID: typing.TypeAlias = str
SinkID: typing.TypeAlias = int


class SinkTypeModel(sqlmodel.SQLModel, table=True):
  __tablename__ = "sink_types"  # type: ignore

  id: SinkTypeID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
  )
  description: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  config_schema: dict[str, typing.Any] = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'{}'::jsonb"),
    ),
  )


class SinkModel(sqlmodel.SQLModel, table=True):
  __tablename__ = "sinks"  # type: ignore

  id: SinkID | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.Identity(),
      primary_key=True,
    ),
  )
  type: SinkTypeID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Text,
      sqlalchemy.ForeignKey("sink_types.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=False,
    )
  )
  nickname: str | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  config: dict[str, typing.Any] = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'{}'::jsonb"),
    ),
  )
  enabled: list[uuid.UUID] = sqlmodel.Field(
    default_factory=list,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.ARRAY(
        sqlalchemy.dialects.postgresql.UUID(as_uuid=True)
      ),
      nullable=False,
      server_default=sqlalchemy.text("'{}'::uuid[]"),
    ),
  )


class SinkCreateForm(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: SinkTypeID
  nickname: str | None = None
  config: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
