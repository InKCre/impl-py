"""Shared receipts for committed capture commands; graph content remains authoritative."""

from datetime import datetime
from uuid import UUID

import sqlalchemy
import sqlmodel


class CaptureReceiptModel(sqlmodel.SQLModel, table=True):
  __tablename__ = "capture_receipts"  # type: ignore
  __table_args__ = (
    sqlalchemy.CheckConstraint(
      "payload_digest ~ '^[0-9a-f]{64}$'", name="capture_receipt_digest"
    ),
    sqlalchemy.CheckConstraint(
      "graph_digest ~ '^[0-9a-f]{64}$'", name="capture_receipt_graph_digest"
    ),
  )

  submission_id: UUID = sqlmodel.Field(primary_key=True)
  payload_digest: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  # NULL belongs only to receipts written before the direct database commit protocol.
  graph_digest: str | None = sqlmodel.Field(
    default=None, sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True)
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
