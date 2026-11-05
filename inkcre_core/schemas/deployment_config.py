"""Deployment-scoped shared configuration persistence and API models."""

import datetime
import typing

import pydantic
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel


DeploymentConfigKey: typing.TypeAlias = str
DeploymentConfigSchemaID: typing.TypeAlias = str


class DeploymentConfigModel(sqlmodel.SQLModel, table=True):
  """One deployment-owned config value under an exact schema contract."""

  __tablename__ = "configs"  # type: ignore

  key: DeploymentConfigKey = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
  )
  schema_id: DeploymentConfigSchemaID = sqlmodel.Field(
    sa_column=sqlalchemy.Column("schema", sqlalchemy.Text, nullable=False)
  )
  value: dict[str, typing.Any] = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
    )
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


class DeploymentConfigReplaceForm(pydantic.BaseModel):
  """Complete PUT body; schema changes are allowed only through this form."""

  model_config = pydantic.ConfigDict(populate_by_name=True)

  schema_id: DeploymentConfigSchemaID = pydantic.Field(alias="schema")
  value: dict[str, typing.Any]


class DeploymentConfigView(pydantic.BaseModel):
  """Stored deployment config and metadata; reading does not revalidate value."""

  model_config = pydantic.ConfigDict(populate_by_name=True)

  key: DeploymentConfigKey
  schema_id: DeploymentConfigSchemaID = pydantic.Field(alias="schema")
  value: dict[str, typing.Any]
  created_at: datetime.datetime
  updated_at: datetime.datetime
