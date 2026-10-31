from app.contracts.jobs import (
  SourceCollectParameters as SourceCollectParameters,
  SourceBackfillParameters as SourceBackfillParameters,
)

__all__ = [
  "SourceID",
  "SourceModel",
  "SourceTypesModel",
  "SourceCollectParameters",
  "SourceBackfillParameters",
]

import datetime
import pydantic
import sqlalchemy
import sqlalchemy.dialects
import sqlalchemy.dialects.postgresql
import sqlmodel
import typing
from typing import Optional as Opt

from app.schemas.info_base.block import BlockID
from app.schemas.info_base.storage import StorageID

SourceID: typing.TypeAlias = int


class SourceCreateForm(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  type: str
  nickname: str | None = None
  config: dict = pydantic.Field(default_factory=dict)
  storage: StorageID | None = None


class SourceUpdateForm(pydantic.BaseModel):
  """Only submitted fields are written; config is replaced, not deep-merged."""

  model_config = pydantic.ConfigDict(extra="forbid")

  nickname: str | None = None
  config: dict = pydantic.Field(default_factory=dict)
  storage: StorageID | None = None


class CollectAt(sqlmodel.SQLModel):
  """Historical migration-only value retained for the append-only baseline."""

  day_of_week: int | None = None
  hour: int = 0
  minute: int = 0


class CollectAtType(sqlmodel.TypeDecorator):
  """Historical migration-only PostgreSQL type; not a current Source field."""

  impl = sqlalchemy.dialects.postgresql.JSONB
  cache_ok = True

  def process_bind_param(self, value, dialect):
    del dialect
    return value.model_dump() if isinstance(value, CollectAt) else value

  def process_result_value(self, value, dialect):
    del dialect
    return CollectAt.model_validate(value) if value else None


class SourceTypesModel(sqlmodel.SQLModel, table=True):
  __tablename__: str = "sources_types"  # type: ignore

  id: str = sqlmodel.Field(sa_column=sqlalchemy.Column(sqlalchemy.Text, primary_key=True))
  """Type of source.
    
    Stable execution identity. Historical values retain their original Python path.
    """
  description: str = sqlmodel.Field(sa_column=sqlalchemy.Column(sqlalchemy.Text))
  """Description of this source type."""
  config_schema: dict = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      server_default=sqlalchemy.text("'{}'::jsonb"),
      nullable=False,
    ),
    default=dict,
  )
  collect_config_schema: dict = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      server_default=sqlalchemy.text("'{}'::jsonb"),
      nullable=False,
    ),
    default=dict,
  )
  backfill_config_schema: dict | None = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=True,
    ),
    default=None,
  )


class SourceModel(sqlmodel.SQLModel, table=True):
  __tablename__: str = "sources"  # type: ignore

  id: Opt[SourceID] = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Integer, primary_key=True, autoincrement=True),
    default=None,
  )
  type: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Text,
      sqlalchemy.ForeignKey("sources_types.id", onupdate="CASCADE", ondelete="CASCADE"),
    )
  )
  """Type of source.
    
    Stable execution identity, independent of the implementing class location.
    When delete a source type, all sources of this type will be deleted too.
    """
  nickname: Opt[str] = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  config: dict = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      server_default=sqlalchemy.text("'{}'::jsonb"),
      nullable=False,
    ),
    default=dict,
  )
  storage: Opt[StorageID] = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Integer,
      sqlalchemy.ForeignKey("storages.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=True,
    ),
  )
  block: Opt[BlockID] = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Integer,
      sqlalchemy.ForeignKey("blocks.id", onupdate="CASCADE", ondelete="SET NULL"),
      nullable=True,
      unique=True,
    ),
  )
  state: dict = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      server_default=sqlalchemy.text("'{}'::jsonb"),
      nullable=False,
    ),
    default_factory=dict,
  )
  """Store source-specific state (e.g., last_uid for mail,
    latest_tweet_id for twitter)."""
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
