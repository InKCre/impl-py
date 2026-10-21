"""Shared receipts for committed capture commands; graph content remains authoritative."""

from datetime import datetime
from typing import Literal
from uuid import UUID

import pydantic
import sqlalchemy
import sqlmodel


class CaptureReceiptModel(sqlmodel.SQLModel, table=True):
  __tablename__ = "capture_receipts"  # type: ignore
  __table_args__ = (
    sqlalchemy.CheckConstraint(
      "payload_digest ~ '^[0-9a-f]{64}$'", name="capture_receipt_digest"
    ),
  )

  submission_id: UUID = sqlmodel.Field(primary_key=True)
  payload_digest: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  # A historical reference must survive deletion, or delayed retries resurrect content.
  root_block_id: int = sqlmodel.Field(nullable=False)
  committed_at: datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )


class CaptureReceipt(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(frozen=True, from_attributes=True)

  submission_id: UUID
  payload_digest: str
  root_block_id: int
  committed_at: datetime


class CaptureResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(frozen=True)

  submission_id: UUID
  root_block_id: int
  committed_at: datetime
  availability: Literal["available", "deleted"]
