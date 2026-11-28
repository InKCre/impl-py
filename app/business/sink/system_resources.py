"""Allowlisted read views over system management authorities."""

import asyncio
import typing
import uuid

import pydantic

from app.business.extension import EXTENSION_HOST
from app.business.extension.state import (
  ExtensionParticipation,
  ExtensionStateService,
  InstalledExtension,
)
from inkcre_core.business.agent import AgentManager
from inkcre_core.business.ai import AIManager
from inkcre_core.business.cron import CronManager
from inkcre_core.business.job import JobManager
from inkcre_core.business.lexical_retrieval import (
  LEXICAL_MAINTAIN_JOB_TYPE,
  LEXICAL_REBUILD_JOB_TYPE,
  LexicalRetrievalManager,
)
from inkcre_core.business.peer import PeerManager
from inkcre_core.business.semantic_retrieval import SemanticRetrievalManager
from inkcre_core.business.source import SourceManager
from inkcre_core.business.source.job import (
  SOURCE_BACKFILL_JOB_TYPE,
  SOURCE_COLLECT_JOB_TYPE,
)
from inkcre_core.schemas.cron import CronModel
from inkcre_core.schemas.job import JobModel, JobStatus
from inkcre_core.schemas.sink import SinkModel
from inkcre_core.schemas.source import SourceModel

from .errors import SinkNotFoundError
from .main import SinkManager


ResourceCollection = typing.Literal[
  "sources",
  "jobs",
  "crons",
  "peers",
  "extensions",
  "sinks",
  "agents",
  "ai_models",
  "embedding_profiles",
]
ResourceID = typing.Annotated[str, pydantic.Field(min_length=1, max_length=256)]
BigIntID = typing.Annotated[int, pydantic.Field(gt=0, le=9223372036854775807)]
LEXICAL_JOB_TYPES = {
  "maintain": LEXICAL_MAINTAIN_JOB_TYPE,
  "rebuild": LEXICAL_REBUILD_JOB_TYPE,
}
_SOURCE_JOB_TYPES = {SOURCE_COLLECT_JOB_TYPE, SOURCE_BACKFILL_JOB_TYPE}
_TELEGRAM_SOURCE = "extensions.telegram.source.Source"


class JobFilters(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  type: typing.Annotated[str, pydantic.Field(min_length=1, max_length=256)] | None = None
  status: JobStatus | None = None
  source: BigIntID | None = None


def _fields(row: pydantic.BaseModel, *keys: str) -> dict:
  return row.model_dump(mode="json", include=set(keys))


def _ints(value: object, *keys: str) -> dict:
  if not isinstance(value, dict):
    return {}
  return {key: value[key] for key in keys if type(value.get(key)) is int}


def _source(row: SourceModel) -> dict:
  result = _fields(
    row, "id", "type", "nickname", "storage", "block", "created_at", "updated_at"
  )
  result["registered_locally"] = SourceManager.has_source_type(row.type)
  if row.type == _TELEGRAM_SOURCE:
    config = _ints(row.config, "bound_user_id")
    if type(row.config.get("download_attachments")) is bool:
      config["download_attachments"] = row.config["download_attachments"]
    result["config"] = config
    result["state"] = _ints(row.state, "bot_id", "last_update_id")
  return result


def _parameters(job_type: str, value: dict) -> dict:
  if job_type in _SOURCE_JOB_TYPES:
    return _ints(value, "source")
  if job_type in LEXICAL_JOB_TYPES.values():
    return {
      "options": _ints(
        value.get("options"),
        "max_records",
        "scan_page_size",
        "diagnostic_limit",
        "after_block",
      )
    }
  return {}


def _diagnostics(entries: object, *, lexical: bool) -> list[dict]:
  if not isinstance(entries, (list, tuple)):
    return []
  result = []
  for entry in entries[:20]:
    if not isinstance(entry, dict):
      continue
    if lexical and entry.get("outcome") in ("unavailable", "failed"):
      known = ("unknown_resolver", "unsupported_lexical_text", "empty_label")
      reason = entry.get("reason")
      result.append(
        {
          **_ints(entry, "block"),
          "outcome": entry["outcome"],
          "reason": reason if reason in known else "projection_failed",
        }
      )
    if not lexical and entry.get("scope") in (
      "primary",
      "materialization",
      "acknowledgement",
      "failure_notification",
    ):
      result.append({**_ints(entry, "update_id"), "scope": entry["scope"]})
  return result


def job_view(row: JobModel) -> dict:
  result = _fields(
    row,
    "id",
    "type",
    "status",
    "created_at",
    "started_at",
    "closed_at",
    "timeout_seconds",
    "abort_requested",
  )
  result["parameters"] = _parameters(row.type, row.parameters)
  state = row.state
  if row.type in LEXICAL_JOB_TYPES.values():
    report = _ints(state, "indexed", "unavailable", "failed", "next_after_block")
    if "next_after_block" in state and state["next_after_block"] is None:
      report["next_after_block"] = None
    if "indexed" in report:
      report["diagnostics"] = _diagnostics(state.get("diagnostics"), lexical=True)
    result["state"] = report
  elif row.type in _SOURCE_JOB_TYPES:
    result["state"] = {
      "counts": _ints(
        state.get("counts"),
        "saved",
        "partial",
        "unsupported",
        "unauthorized",
        "duplicates",
        "failed",
        "collected",
        "skipped",
      ),
      "diagnostics": _diagnostics(state.get("diagnostics"), lexical=False),
    }
  # Worker errors can contain provider credentials. Categorical status is public
  # to this view; raw messages and traces belong to deployment diagnostics.
  if "error" in state:
    result["failure_details_recorded"] = True
  return result


def _cron(row: CronModel) -> dict:
  return {
    **_fields(
      row,
      "id",
      "schedule",
      "enabled",
      "job_type",
      "job_timeout_seconds",
      "last_job",
      "last_scheduled_for",
      "created_at",
      "updated_at",
    ),
    "job_parameters": _parameters(row.job_type, row.job_parameters),
  }


def _sink(row: SinkModel) -> dict:
  return {
    **_fields(row, "id", "type", "nickname", "enabled"),
    "running_locally": row.id is not None and SinkManager.get_running(row.id) is not None,
  }


def _extension(row: InstalledExtension, selected: ExtensionParticipation | None) -> dict:
  running = EXTENSION_HOST.running.get(row.name)
  return {
    "id": row.name,
    "nickname": row.nickname,
    "local_selection": (
      _fields(selected, "peer_id", "distribution", "version", "enabled")
      if selected is not None
      else None
    ),
    "running_version_locally": running.version if running is not None else None,
  }


def _peer(row: dict) -> dict:
  keys = ("id", "name", "application_version", "labels", "lease_active", "lease_expires_at")
  return {key: row[key] for key in keys}


_NUMERIC_READERS: dict[
  str,
  tuple[
    typing.Callable[[int], typing.Awaitable[typing.Any]],
    typing.Callable[..., typing.Awaitable[tuple[typing.Sequence[typing.Any], int | None]]],
    typing.Callable[[typing.Any], dict],
  ],
] = {
  "sources": (SourceManager.get, SourceManager.list_sources, _source),
  "crons": (CronManager.get, CronManager.list_crons, _cron),
  "sinks": (SinkManager.get, SinkManager.page, _sink),
  "agents": (
    AgentManager.get_definition,
    AgentManager.list_definitions,
    lambda row: _fields(row, "id", "name", "model", "tools", "created_at", "updated_at"),
  ),
  "ai_models": (
    AIManager.get_model,
    AIManager.list_models,
    lambda row: _fields(
      row,
      "id",
      "name",
      "provider",
      "native_model_id",
      "enabled",
      "capabilities",
      "created_at",
      "updated_at",
    ),
  ),
  "embedding_profiles": (
    SemanticRetrievalManager.get_profile,
    SemanticRetrievalManager.list_profiles,
    lambda row: _fields(
      row, "id", "name", "ai_model", "dimensions", "created_at", "updated_at"
    ),
  ),
}


def _page(rows: list[dict], cursor: int | str | uuid.UUID | None) -> dict:
  return {"results": rows, "next_cursor": str(cursor) if cursor is not None else None}


async def read_system_resources(
  collection: ResourceCollection,
  *,
  id_: str | None,
  cursor: str | None,
  limit: int,
  job_filters: JobFilters | None,
) -> dict:
  if id_ is not None and (cursor is not None or job_filters is not None):
    raise ValueError("Use id by itself, or use cursor and filters for a list")
  if job_filters is not None and collection != "jobs":
    raise ValueError("Job filters apply only to jobs")

  if collection == "extensions":
    store = ExtensionStateService()
    if id_ is not None:
      extension = await store.get(id_)
      extensions, next_cursor = (extension,) if extension else (), None
    else:
      extensions, next_cursor = await store.page(limit=limit, cursor=cursor)
    if not extensions:
      return _page([], next_cursor)
    selections = await store.list_participations(
      PeerManager.get_current_peer_ref(), names=tuple(row.name for row in extensions)
    )
    selected = {row.name: row for row in selections}
    return _page(
      [_extension(row, selected.get(row.name)) for row in extensions], next_cursor
    )

  if collection == "peers":
    if id_ is not None:
      peer = await PeerManager.get_with_lease(uuid.UUID(id_))
      return _page([_peer(peer)] if peer else [], None)
    peers, next_peer = await PeerManager.list_with_leases(
      limit=limit, cursor=uuid.UUID(cursor) if cursor is not None else None
    )
    return _page([_peer(row) for row in peers], next_peer)

  numeric_id = (
    pydantic.TypeAdapter(BigIntID).validate_python(id_) if id_ is not None else None
  )
  after = (
    pydantic.TypeAdapter(BigIntID).validate_python(cursor) if cursor is not None else None
  )
  if collection == "jobs":
    if numeric_id is not None:
      job = await JobManager.get(numeric_id)
      return _page([job_view(job)] if job else [], None)
    filters = job_filters or JobFilters()
    jobs, next_job = await JobManager.list_jobs(
      limit=limit,
      cursor=after,
      type_=filters.type,
      status=filters.status,
      source=filters.source,
    )
    return _page([job_view(row) for row in jobs], next_job)

  get, page, project = _NUMERIC_READERS[collection]
  if numeric_id is not None:
    try:
      row = await get(numeric_id)
    except SinkNotFoundError:
      row = None
    return _page([project(row)] if row is not None else [], None)
  rows, next_id = await page(limit=limit, cursor=after)
  return _page([project(row) for row in rows], next_id)


async def lexical_job_status() -> dict:
  async def latest(job_type: str, status: JobStatus | None = None) -> dict | None:
    rows, _ = await JobManager.list_jobs(type_=job_type, status=status, limit=1)
    return job_view(rows[0]) if rows else None

  async def by_operation(job_type: str) -> dict:
    newest, pending, running = await asyncio.gather(
      latest(job_type),
      latest(job_type, JobStatus.PENDING),
      latest(job_type, JobStatus.RUNNING),
    )
    return {"latest": newest, "pending": pending, "running": running}

  maintain, rebuild, missing_or_stale = await asyncio.gather(
    by_operation(LEXICAL_MAINTAIN_JOB_TYPE),
    by_operation(LEXICAL_REBUILD_JOB_TYPE),
    LexicalRetrievalManager.has_missing_or_stale_projection(),
  )
  return {
    "maintain": maintain,
    "rebuild": rebuild,
    "has_missing_or_stale_projection": missing_or_stale,
  }
