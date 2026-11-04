"""Bounded, backend-independent observability records and queries."""

from datetime import datetime, timezone
from typing import Literal, Self
import math
import uuid

from pydantic import (
  BaseModel,
  ConfigDict,
  Field,
  JsonValue,
  field_validator,
  model_validator,
)

BackendName = Literal["postgresql", "loki"]
RecordKind = Literal["log", "event", "span"]
KIND_KEY = "inkcre.record.kind"
EVENT_KEY = "inkcre.event.name"
ID_KEY = "inkcre.record.id"


class ObservationRecord(BaseModel):
  model_config = ConfigDict(extra="forbid")
  id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=128)
  timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
  kind: RecordKind = "log"
  event_name: str | None = Field(default=None, max_length=128)
  severity_number: int = Field(default=9, ge=1, le=24)
  severity_text: str = Field(default="INFO", max_length=32)
  body: str = Field(max_length=8192)
  trace_id: str | None = Field(default=None, max_length=128)
  span_id: str | None = Field(default=None, max_length=128)
  attributes: dict[str, JsonValue] = Field(default_factory=dict, max_length=64)

  @field_validator("timestamp")
  @classmethod
  def aware_time(cls, value: datetime) -> datetime:
    if value.tzinfo is None:
      raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)

  @field_validator("attributes")
  @classmethod
  def bounded_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    for key, item in value.items():
      if len(key) > 128:
        raise ValueError("attribute name exceeds 128 characters")
      values = item if isinstance(item, list) else [item]
      if len(values) > 32:
        raise ValueError("attribute array exceeds 32 values")
      for part in values:
        if isinstance(part, (dict, list)):
          raise ValueError("attributes contain only scalar values or scalar arrays")
        if isinstance(part, str) and len(part) > 1024:
          raise ValueError("attribute value exceeds 1024 characters")
        if isinstance(part, float) and not math.isfinite(part):
          raise ValueError("attribute numbers must be finite")
    return value


class ObservationQuery(BaseModel):
  model_config = ConfigDict(extra="forbid")
  since: datetime
  until: datetime
  limit: int = Field(default=100, ge=1, le=500)
  kind: RecordKind | None = None
  severity_min: int | None = Field(default=None, ge=1, le=24)
  job_id: int | None = Field(default=None, ge=1)
  trace_id: str | None = Field(default=None, max_length=128)
  peer_id: uuid.UUID | None = None
  operation: str | None = Field(default=None, max_length=128)
  text: str | None = Field(default=None, max_length=256)

  @field_validator("since", "until")
  @classmethod
  def aware_time(cls, value: datetime) -> datetime:
    if value.tzinfo is None:
      raise ValueError("query times must include a timezone")
    return value.astimezone(timezone.utc)

  @model_validator(mode="after")
  def ordered_window(self) -> Self:
    if self.since >= self.until:
      raise ValueError("since must precede until")
    if (self.until - self.since).total_seconds() > 31 * 86400:
      raise ValueError("query window cannot exceed 31 days")
    return self


class ObservationPage(BaseModel):
  records: list[ObservationRecord]
  truncated: bool
  since: datetime
  until: datetime
  backend: BackendName


class ObservationStatus(BaseModel):
  backend: BackendName
  configured: bool
  recording: bool
  queryable: bool
  scope: Literal["selected_backend"] = "selected_backend"
  history: str = "Only the selected backend is queried; prior backend data remains there."
  dropped: int
  error: str | None


class ObservationRequest(BaseModel):
  model_config = ConfigDict(extra="forbid")
  action: Literal["status", "query", "ingest"]
  expected_backend: BackendName | None = None
  query: ObservationQuery | None = None
  records: list[ObservationRecord] | None = Field(default=None, max_length=100)

  @model_validator(mode="after")
  def action_payload(self) -> Self:
    if (self.action == "query") != (self.query is not None):
      raise ValueError("query is required only for the query action")
    if (self.action == "ingest") != (self.records is not None):
      raise ValueError("records are required only for the ingest action")
    return self
