"""Explicit PostgreSQL and Loki storage; neither selects an application backend."""

from collections.abc import Sequence
from datetime import datetime, timezone
import json
import re
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict
import sqlalchemy as sa
from sqlmodel import col
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .log_record import LogModel
from .records import (
  BackendName,
  EVENT_KEY,
  ID_KEY,
  KIND_KEY,
  ObservationPage,
  ObservationQuery,
  ObservationRecord,
)


def _nanoseconds(value: datetime) -> int:
  delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
  return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


class ObservabilityUnavailable(RuntimeError):
  """Selected backend failed; callers must not reinterpret this as empty results."""


class ObservationBackend(Protocol):
  name: BackendName

  async def write(self, records: Sequence[ObservationRecord]) -> None: ...
  async def query(self, query: ObservationQuery) -> ObservationPage: ...
  async def aclose(self) -> None: ...


class PostgreSQLBackend:
  name: BackendName = "postgresql"

  def __init__(self, sessions: async_sessionmaker[AsyncSession]):
    self.sessions = sessions

  async def write(self, records: Sequence[ObservationRecord]) -> None:
    async with self.sessions.begin() as session:
      session.add_all(
        [
          LogModel(
            timestamp=record.timestamp,
            severity_number=record.severity_number,
            severity_text=record.severity_text,
            body=record.body,
            trace_id=record.trace_id,
            span_id=record.span_id,
            attributes={
              **record.attributes,
              KIND_KEY: record.kind,
              EVENT_KEY: record.event_name,
              ID_KEY: record.id,
            },
          )
          for record in records
        ]
      )

  async def query(self, query: ObservationQuery) -> ObservationPage:
    statement = sa.select(LogModel).where(
      col(LogModel.timestamp) >= query.since,
      col(LogModel.timestamp) < query.until,
    )
    attrs = col(LogModel.attributes)
    if query.kind is not None:
      statement = statement.where(
        sa.func.coalesce(attrs[KIND_KEY].astext, "log") == query.kind
      )
    if query.severity_min is not None:
      statement = statement.where(col(LogModel.severity_number) >= query.severity_min)
    if query.job_id is not None:
      statement = statement.where(
        sa.or_(
          attrs["inkcre.job.id"].astext == str(query.job_id),
          attrs["job_id"].astext == str(query.job_id),
          col(LogModel.trace_id) == f"job.{query.job_id}",
        )
      )
    if query.trace_id is not None:
      statement = statement.where(col(LogModel.trace_id) == query.trace_id)
    for key, value in (
      ("inkcre.peer.id", str(query.peer_id) if query.peer_id is not None else None),
      ("inkcre.operation", query.operation),
    ):
      if value is not None:
        statement = statement.where(attrs[key].astext == value)
    if query.text:
      escaped = query.text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
      statement = statement.where(col(LogModel.body).ilike(f"%{escaped}%", escape="\\"))
    statement = statement.order_by(
      col(LogModel.timestamp).desc(), col(LogModel.id).desc()
    ).limit(query.limit + 1)
    async with self.sessions() as session:
      rows = (await session.execute(statement)).scalars().all()
    # Historical PostgreSQL records may contain arbitrary nested logger extras.
    # Preserve them for display; new ingress is validated by ObservationRecord.
    records = [
      ObservationRecord.model_construct(
        id=str(row.attributes.get(ID_KEY) or f"pg.{row.id}"),
        timestamp=row.timestamp.astimezone(timezone.utc),
        kind=row.attributes.get(KIND_KEY, "log"),
        event_name=row.attributes.get(EVENT_KEY),
        severity_number=row.severity_number,
        severity_text=row.severity_text,
        body=row.body,
        trace_id=row.trace_id,
        span_id=row.span_id,
        attributes={
          k: v for k, v in row.attributes.items() if k not in (ID_KEY, KIND_KEY, EVENT_KEY)
        },
      )
      for row in rows[: query.limit]
    ]
    return ObservationPage(
      records=records,
      truncated=len(rows) > query.limit,
      since=query.since,
      until=query.until,
      backend=self.name,
    )

  async def aclose(self) -> None:
    # The consumer owns the session factory and engine.
    pass


class _LokiStream(BaseModel):
  model_config = ConfigDict(extra="ignore")
  values: list[tuple[str, str]]


class _LokiData(BaseModel):
  model_config = ConfigDict(extra="ignore")
  resultType: str
  result: list[_LokiStream]


class _LokiResponse(BaseModel):
  model_config = ConfigDict(extra="ignore")
  status: str
  data: _LokiData


class LokiBackend:
  """Loki HTTP API with one deployment stream and private application-owned auth.

  The optional client is borrowed. URL and credentials never enter query payloads
  or status output. This stores normalized records, not arbitrary OTLP signals.
  """

  name: BackendName = "loki"

  def __init__(
    self,
    *,
    url: str,
    deployment_id: str,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
  ):
    self.url = url.rstrip("/")
    self.deployment_id = deployment_id
    self._owns_client = client is None
    self.client = client or httpx.AsyncClient(
      headers=headers,
      timeout=10,
      follow_redirects=False,
    )

  async def write(self, records: Sequence[ObservationRecord]) -> None:
    # Flat indexing fields are transport projections; the canonical record is nested.
    values = []
    for record in records:
      projected = {
        "record": record.model_dump(mode="json"),
        "kind": record.kind,
        "severity": record.severity_number,
        "trace": record.trace_id or "",
        "peer": record.attributes.get("inkcre.peer.id", ""),
        "job": str(
          record.attributes.get("inkcre.job.id", record.attributes.get("job_id", ""))
        ),
        "operation": record.attributes.get("inkcre.operation", ""),
        "body": record.body,
      }
      values.append([str(_nanoseconds(record.timestamp)), json.dumps(projected)])
    response = await self.client.post(
      self.url + "/loki/api/v1/push",
      json={
        "streams": [
          {
            "stream": {"service_name": "inkcre", "deployment_id": self.deployment_id},
            "values": values,
          }
        ]
      },
    )
    if response.status_code not in (200, 204):
      raise ObservabilityUnavailable("Selected observability backend rejected records")

  async def query(self, query: ObservationQuery) -> ObservationPage:
    selector = (
      '{service_name="inkcre",deployment_id=' + json.dumps(self.deployment_id) + "} | json"
    )
    for key, value in (
      ("kind", query.kind),
      ("trace", query.trace_id),
      ("peer", str(query.peer_id) if query.peer_id is not None else None),
      ("operation", query.operation),
    ):
      if value is not None:
        selector += f" | {key} = {json.dumps(value)}"
    if query.job_id is not None:
      selector += (
        f" | job = {json.dumps(str(query.job_id))}"
        f" or trace = {json.dumps(f'job.{query.job_id}')}"
      )
    if query.severity_min is not None:
      selector += f" | severity >= {query.severity_min}"
    if query.text:
      # The line contains JSON, so filtering the decoded body avoids matching keys.
      selector += " | body =~ " + json.dumps("(?is).*" + re.escape(query.text) + ".*")
    async with self.client.stream(
      "GET",
      self.url + "/loki/api/v1/query_range",
      params={
        "query": selector,
        "start": str(_nanoseconds(query.since)),
        "end": str(_nanoseconds(query.until) - 1),
        "direction": "backward",
        "limit": query.limit + 1,
      },
    ) as response:
      if response.status_code != 200:
        raise ObservabilityUnavailable("Selected observability backend query failed")
      body = bytearray()
      async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > 8 * 1024 * 1024:
          raise ObservabilityUnavailable(
            "Selected observability backend response exceeded limit"
          )
    result = _LokiResponse.model_validate_json(body)
    if result.status != "success" or result.data.resultType != "streams":
      raise ObservabilityUnavailable(
        "Selected observability backend returned an invalid result"
      )
    records = [
      ObservationRecord.model_validate(json.loads(line)["record"])
      for stream in result.data.result
      for _, line in stream.values
    ]
    records.sort(key=lambda record: (record.timestamp, record.id), reverse=True)
    return ObservationPage(
      records=records[: query.limit],
      truncated=len(records) > query.limit,
      since=query.since,
      until=query.until,
      backend=self.name,
    )

  async def aclose(self) -> None:
    if self._owns_client:
      await self.client.aclose()
