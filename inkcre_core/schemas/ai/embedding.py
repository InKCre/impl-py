"""Profile-scoped Block and Relation embedding records."""

import datetime

import pgvector.sqlalchemy
import sqlalchemy
import sqlmodel

from inkcre_core.schemas.info_base.main import Vector

from .main import EmbeddingProfileID


class BlockEmbeddingModel(sqlmodel.SQLModel, table=True):
  __tablename__ = "block_embeddings"  # type: ignore

  profile: EmbeddingProfileID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.ForeignKey(
        "embedding_profiles.id", onupdate="CASCADE", ondelete="RESTRICT"
      ),
      primary_key=True,
    )
  )
  block: int = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Integer,
      sqlalchemy.ForeignKey("blocks.id", onupdate="CASCADE", ondelete="CASCADE"),
      primary_key=True,
    )
  )
  embedding: Vector = sqlmodel.Field(
    sa_column=sqlalchemy.Column(pgvector.sqlalchemy.VECTOR(), nullable=False)
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


class RelationEmbeddingModel(sqlmodel.SQLModel, table=True):
  __tablename__ = "relation_embeddings"  # type: ignore

  profile: EmbeddingProfileID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.ForeignKey(
        "embedding_profiles.id", onupdate="CASCADE", ondelete="RESTRICT"
      ),
      primary_key=True,
    )
  )
  relation: int = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Integer,
      sqlalchemy.ForeignKey("relations.id", onupdate="CASCADE", ondelete="CASCADE"),
      primary_key=True,
    )
  )
  embedding: Vector = sqlmodel.Field(
    sa_column=sqlalchemy.Column(pgvector.sqlalchemy.VECTOR(), nullable=False)
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
