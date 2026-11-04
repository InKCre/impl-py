import typing
import uuid
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel
from typing import Optional as Opt


StorageTypeID: typing.TypeAlias = str
StorageID: typing.TypeAlias = int


class StorageBlobModel(sqlmodel.SQLModel, table=True):
  """Raw PostgreSQL-owned bytes addressed by an opaque generated pointer."""

  __tablename__: str = "storage_blobs"  # type: ignore

  id: uuid.UUID = sqlmodel.Field(
    default_factory=uuid.uuid4,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.UUID(as_uuid=True),
      primary_key=True,
      server_default=sqlalchemy.text("gen_random_uuid()"),
    ),
  )
  data: bytes = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.LargeBinary, nullable=False)
  )


class StorageTypesModel(sqlmodel.SQLModel, table=True):
  __tablename__: str = "storage_types"  # type: ignore

  id: StorageTypeID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
  )
  """Type of storage.

  A custom string to identify the storage type.
  For extension storages, must follow `extensions.{extension_id}.{type}` format.
  """
  description: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  """Description of this storage type."""
  config_schema: dict = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      server_default=sqlalchemy.text("'{}'::jsonb"),
      nullable=False,
    ),
    default=dict,
  )
  writable: bool = sqlmodel.Field(
    default=False,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Boolean,
      nullable=False,
      server_default=sqlalchemy.false(),
    ),
  )


class StorageModel(sqlmodel.SQLModel, table=True):
  __tablename__: str = "storages"  # type: ignore

  id: Opt[StorageID] = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Integer, primary_key=True, autoincrement=True),
    default=None,
  )
  type: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Text,
      sqlalchemy.ForeignKey("storage_types.id", onupdate="CASCADE", ondelete="CASCADE"),
      nullable=False,
    )
  )
  """Type of storage.

    An absolute import path to the module where storage class at.
    When delete a storage type, all storages of this type will be deleted too.
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
