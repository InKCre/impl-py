"""Block-local lexical projection, maintenance, and ranked result contracts."""

from app.contracts.jobs import (
  LexicalMaintenanceOptions as LexicalMaintenanceOptions,
  LexicalMaintenanceJobParameters as LexicalMaintenanceJobParameters,
)

import datetime
import math
import typing

import pydantic
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel

from app.schemas.info_base.block import BlockModel


LexicalEvidence: typing.TypeAlias = typing.Literal[
  "label_exact",
  "label_substring",
  "text_substring",
  "terms",
]


class BlockLexicalRecordModel(sqlmodel.SQLModel, table=True):
  """Rebuildable Resolver projection owned only by lexical retrieval."""

  __tablename__ = "block_lexical_records"  # type: ignore
  __table_args__ = (
    sqlalchemy.Index(
      "block_lexical_records_search_vector_idx",
      "search_vector",
      postgresql_using="gin",
    ),
    sqlalchemy.Index(
      "block_lexical_records_label_trgm_idx",
      "label",
      postgresql_using="gin",
      postgresql_ops={"label": "gin_trgm_ops"},
    ),
    sqlalchemy.Index(
      "block_lexical_records_text_trgm_idx",
      "text",
      postgresql_using="gin",
      postgresql_ops={"text": "gin_trgm_ops"},
    ),
  )

  block: int = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Integer,
      sqlalchemy.ForeignKey("blocks.id", onupdate="CASCADE", ondelete="CASCADE"),
      primary_key=True,
    )
  )
  label: str = sqlmodel.Field(sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False))
  text: str | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  search_vector: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.TSVECTOR,
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


class LexicalRetrievalRequest(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  query: str
  limit: int = pydantic.Field(default=20, ge=1, le=20)

  @pydantic.field_validator("query")
  @classmethod
  def non_empty_query(cls, value: str) -> str:
    if not value.strip():
      raise ValueError("query must not be empty")
    return value


class LexicalRetrievalMatch(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  block: BlockModel
  label: str
  excerpt: str
  evidence: LexicalEvidence
  rank: float = pydantic.Field(ge=0)

  @pydantic.field_validator("rank")
  @classmethod
  def finite_rank(cls, value: float) -> float:
    if not math.isfinite(value):
      raise ValueError("rank must be finite")
    return value


class LexicalRetrievalResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  matches: tuple[LexicalRetrievalMatch, ...]


class LexicalMaintenanceDiagnostic(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  block: int
  outcome: typing.Literal["unavailable", "failed"]
  reason: str


class LexicalMaintenanceReport(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  indexed: int = 0
  unavailable: int = 0
  failed: int = 0
  diagnostics: tuple[LexicalMaintenanceDiagnostic, ...] = ()
