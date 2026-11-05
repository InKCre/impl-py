__all__ = ["LogModel", "adapt_log_record", "TRACE_ID", "SPAN_ID", "ENABLE_LOG_BACKEND"]

from contextvars import ContextVar
import logging
import datetime
from typing import Mapping, Any, Dict, Optional as Opt
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
import sqlmodel

from inkcre_core.database_contract.constants import PROTOCOL_SCHEMA


TRACE_ID: ContextVar[str | None] = ContextVar("TRACE_ID", default=None)
SPAN_ID: ContextVar[str | None] = ContextVar("SPAN_ID", default=None)
ENABLE_LOG_BACKEND: ContextVar[bool] = ContextVar("ENABLE_LOG_BACKEND", default=False)
"""Whether the log backend emit the logs or not"""


LOGGING_TO_OTEL: Mapping[int, int] = {
  10: 5,  # DEBUG
  20: 9,  # INFO
  30: 13,  # WARNING
  40: 17,  # ERROR
  50: 21,  # CRITICAL
}


def logging_level_to_otel(levelno: int) -> int:
  return LOGGING_TO_OTEL.get(levelno, 9)


class LogModel(sqlmodel.SQLModel, table=True):
  """Log model"""

  __tablename__ = "logs"  # type: ignore
  __table_args__ = {"schema": PROTOCOL_SCHEMA}

  id: Opt[int] = sqlmodel.Field(
    default=None,
    sa_column=sa.Column(sa.BigInteger, primary_key=True, autoincrement=True),
  )
  timestamp: datetime.datetime = sqlmodel.Field(
    default_factory=datetime.datetime.now,
    sa_column=sa.Column(
      sa.TIMESTAMP(timezone=True),
      server_default=sa.text("CURRENT_TIMESTAMP"),
      nullable=False,
    ),
  )
  severity_number: int = sqlmodel.Field(sa_type=sa.SmallInteger)
  severity_text: str = sqlmodel.Field(sa_column=sa.Column(sa.Text, nullable=False))
  body: str = sqlmodel.Field(sa_column=sa.Column(sa.Text, nullable=False))
  trace_id: Opt[str] = sqlmodel.Field(
    default=None,
    sa_column=sa.Column(sa.Text, nullable=True),
  )
  span_id: Opt[str] = sqlmodel.Field(
    default=None,
    sa_column=sa.Column(sa.Text, nullable=True),
  )
  attributes: Dict[str, Any] = sqlmodel.Field(
    sa_type=JSONB,
    default_factory=dict,
    sa_column_kwargs={"server_default": sa.text("'{}'::jsonb")},
  )


def adapt_log_record(
  record: logging.LogRecord,
  # *,
  # service_name: str,
  # service_version: str | None = None,
  # environment: str | None = None,
) -> LogModel:
  attributes = {
    k: v
    for k, v in record.__dict__.items()
    if k
    not in {
      "name",
      "msg",
      "message",
      "args",
      "levelname",
      "levelno",
      "pathname",
      "filename",
      "module",
      "exc_info",
      "exc_text",
      "stack_info",
      "lineno",
      "funcName",
      "created",
      "msecs",
      "relativeCreated",
      "thread",
      "threadName",
      "processName",
      "process",
      "taskName",
      "asctime",
    }
    and not k.startswith("_")
  }

  return LogModel(
    timestamp=datetime.datetime.fromtimestamp(record.created),
    severity_number=logging_level_to_otel(record.levelno),
    severity_text=record.levelname,
    body=record.getMessage(),
    trace_id=TRACE_ID.get(),
    span_id=SPAN_ID.get(),
    attributes=attributes,
    # service_name=service_name,
    # service_version=service_version,
    # environment=environment,
  )
