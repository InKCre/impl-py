"""Checked-in built-in catalog profile.

The profile is data only. Reconciliation can consume it without importing FastAPI,
application settings, extension runtimes, or the scheduler.
"""

from dataclasses import dataclass
from typing import Any

from app.contracts.jobs import (
  BUILTIN_JOB_TYPES as BUILTIN_JOB_TYPES,
  BUILTIN_JOB_TYPES_BY_ID as BUILTIN_JOB_TYPES_BY_ID,
)


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class TypeProfile:
  id: str
  description: str
  config_schema: JsonObject


@dataclass(frozen=True)
class StorageTypeProfile:
  id: str
  description: str
  config_schema: JsonObject
  writable: bool


@dataclass(frozen=True)
class StorageProfile:
  id: int
  type: str
  nickname: str
  config: JsonObject


def _object_schema(
  title: str,
  description: str,
  properties: JsonObject,
) -> JsonObject:
  return {
    "description": description,
    "properties": properties,
    "title": title,
    "type": "object",
  }


def _positive_integer(default: int) -> JsonObject:
  return {"default": default, "exclusiveMinimum": 0, "type": "integer"}


def _boolean(default: bool) -> JsonObject:
  return {"default": default, "type": "boolean"}


HTTP_STORAGE_SCHEMA = _object_schema(
  "HTTPStorageConfig",
  "Configuration for HTTP storage.",
  {
    "follow_redirects": _boolean(True),
    "max_response_bytes": _positive_integer(64 * 1024 * 1024),
    "timeout": _positive_integer(30),
  },
)

OPENAI_COMPATIBLE_DIALECT_SCHEMA = {
  "additionalProperties": False,
  "description": "Connection values for one OpenAI-compatible provider instance.",
  "properties": {
    "api_key": {
      "minLength": 1,
      "title": "Api Key",
      "type": "string",
    },
    "base_url": {
      "anyOf": [
        {"minLength": 1, "type": "string"},
        {"type": "null"},
      ],
      "default": None,
      "title": "Base Url",
    },
  },
  "required": ["api_key"],
  "title": "OpenAICompatibleConfig",
  "type": "object",
}

BUILTIN_AI_DIALECTS = (
  TypeProfile(
    "core.openai-compatible.v1",
    "OpenAI-compatible embedding and chat protocol.",
    OPENAI_COMPATIBLE_DIALECT_SCHEMA,
  ),
  TypeProfile(
    "core.alibaba-model-studio.v1",
    "Alibaba Model Studio OpenAI-compatible multimodal chat protocol.",
    OPENAI_COMPATIBLE_DIALECT_SCHEMA,
  ),
)

BUILTIN_STORAGE_TYPES = (
  StorageTypeProfile(
    "http",
    "HTTP storage for bounded retrieval of opaque response bytes.",
    HTTP_STORAGE_SCHEMA,
    False,
  ),
)

BUILTIN_STORAGE_TYPES += (
  StorageTypeProfile(
    "postgresql_binary",
    "PostgreSQL storage for deployment-owned binary content.",
    _object_schema(
      "PostgreSQLBinaryStorageConfig",
      "Configuration for PostgreSQL binary storage.",
      {},
    ),
    True,
  ),
)


BUILTIN_STORAGES = (
  StorageProfile(-1, "http", "HTTP", {}),
  StorageProfile(-4, "postgresql_binary", "PostgreSQL Binary", {}),
)

BUILTIN_AI_DIALECTS_BY_ID = {item.id: item for item in BUILTIN_AI_DIALECTS}
BUILTIN_STORAGE_TYPES_BY_ID = {item.id: item for item in BUILTIN_STORAGE_TYPES}
