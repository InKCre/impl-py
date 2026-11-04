"""Shared AI dialect, provider, model and embedding-profile facts."""

import datetime
import typing

import pydantic
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel

from .capability import CAPABILITIES_ADAPTER, AIModelCapability, normalize_capabilities


AIDialectID: typing.TypeAlias = str
AIProviderID: typing.TypeAlias = int
AIModelID: typing.TypeAlias = int
EmbeddingProfileID: typing.TypeAlias = int


class AICapabilitiesType(sqlalchemy.TypeDecorator):
  """Round-trip typed, canonical capability declarations through JSONB."""

  impl = sqlalchemy.dialects.postgresql.JSONB
  cache_ok = True

  def process_bind_param(self, value, dialect):
    del dialect
    return [
      capability.model_dump(mode="json") for capability in normalize_capabilities(value)
    ]

  def process_result_value(self, value, dialect):
    del dialect
    return CAPABILITIES_ADAPTER.validate_python(value or ())


class AIDialectModel(sqlmodel.SQLModel, table=True):
  """One exact shared AI wire/config dialect contract."""

  __tablename__ = "ai_dialects"  # type: ignore

  id: AIDialectID = sqlmodel.Field(
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


class AIProviderModel(sqlmodel.SQLModel, table=True):
  """One deployment-scoped configuration of an AI dialect."""

  __tablename__ = "ai_providers"  # type: ignore

  id: AIProviderID | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.Identity(),
      primary_key=True,
    ),
  )
  name: str = sqlmodel.Field(sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False))
  dialect: AIDialectID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Text,
      sqlalchemy.ForeignKey("ai_dialects.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=False,
    )
  )
  config: dict[str, typing.Any] = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'{}'::jsonb"),
    ),
  )
  enabled: bool = sqlmodel.Field(
    default=True,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Boolean,
      nullable=False,
      server_default=sqlalchemy.true(),
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
  updated_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )


class AIModelModel(sqlmodel.SQLModel, table=True):
  """One provider-bound native model offering and its effective capabilities."""

  __tablename__ = "ai_models"  # type: ignore
  __table_args__ = (
    sqlalchemy.UniqueConstraint(
      "provider", "native_model_id", name="uq_ai_models_provider_native_model"
    ),
  )

  id: AIModelID | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.Identity(),
      primary_key=True,
    ),
  )
  provider: AIProviderID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.ForeignKey("ai_providers.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=False,
    )
  )
  native_model_id: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  name: str | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  capabilities: tuple[AIModelCapability, ...] = sqlmodel.Field(
    default=(),
    sa_column=sqlalchemy.Column(AICapabilitiesType(), nullable=False),
  )
  enabled: bool = sqlmodel.Field(
    default=True,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Boolean,
      nullable=False,
      server_default=sqlalchemy.true(),
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
  updated_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )

  @pydantic.field_validator("capabilities", mode="before")
  @classmethod
  def canonical_capabilities(cls, value: typing.Any) -> tuple[AIModelCapability, ...]:
    return normalize_capabilities(value)


class EmbeddingProfileModel(sqlmodel.SQLModel, table=True):
  """One durable vector-space compatibility contract."""

  __tablename__ = "embedding_profiles"  # type: ignore
  __table_args__ = (
    sqlalchemy.CheckConstraint(
      "dimensions > 0", name="ck_embedding_profiles_dimensions_positive"
    ),
  )

  id: EmbeddingProfileID | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.Identity(),
      primary_key=True,
    ),
  )
  name: str | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  ai_model: AIModelID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.ForeignKey("ai_models.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=False,
    )
  )
  dimensions: int = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Integer, nullable=False)
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
