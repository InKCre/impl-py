"""Deterministic, language-neutral projection of the admitted database protocol."""

from __future__ import annotations

from typing import Any, cast

import sqlalchemy
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.sql.schema import Column, ForeignKeyConstraint, Table
from sqlalchemy.sql.type_api import TypeEngine

from migrations.metadata import get_target_metadata

from .constants import PROTOCOL_SCHEMA


PROTOCOL_DOCUMENT_FORMAT = 1
POSTGREST_OCTET_STREAM_TYPE = f'{PROTOCOL_SCHEMA}."application/octet-stream"'

PROTOCOL_FUNCTIONS: dict[str, dict[str, object]] = {
  "create_storage_blob": {
    "arguments": [
      {
        "name": None,
        "type": {"kind": "string", "format": "bytea"},
      }
    ],
    "returns": {"kind": "string", "format": "uuid"},
    "returns_set": False,
    "volatility": "volatile",
    "request_media_type": "application/octet-stream",
  },
  "read_storage_blob": {
    "arguments": [
      {
        "name": "blob_id",
        "type": {"kind": "string", "format": "uuid"},
      }
    ],
    "returns": {
      "kind": "string",
      "format": "bytea",
      "database_type": POSTGREST_OCTET_STREAM_TYPE,
    },
    "returns_set": False,
    "volatility": "stable",
    "response_media_type": "application/octet-stream",
  },
  "renew_peer_lease": {
    "arguments": [
      {
        "name": "peer",
        "type": {"kind": "string", "format": "uuid"},
      },
      {
        "name": "ttl_seconds",
        "type": {"kind": "number", "format": "integer"},
      },
    ],
    "returns": {
      "kind": "string",
      "format": "date-time",
      "database_type": "timestamp with time zone",
    },
    "returns_set": False,
    "volatility": "volatile",
  },
}


def _database_type_name(type_document: dict[str, object]) -> str:
  explicit = type_document.get("database_type")
  if isinstance(explicit, str):
    return explicit
  format_ = type_document.get("format")
  if isinstance(format_, str):
    return format_
  kind = type_document.get("kind")
  if kind == "string":
    return "text"
  if kind == "boolean":
    return "boolean"
  if kind == "number":
    return "numeric"
  raise TypeError(f"unsupported database function type: {type_document!r}")


def protocol_database_function_signatures() -> dict[
  str,
  tuple[tuple[str, ...], tuple[str, ...], str, bool, str],
]:
  """Project exact PostgreSQL signatures from the published protocol document."""
  volatility_codes = {"immutable": "i", "stable": "s", "volatile": "v"}
  signatures = {}
  for function_name, function_document in PROTOCOL_FUNCTIONS.items():
    arguments = cast(list[dict[str, object]], function_document["arguments"])
    returns = cast(dict[str, object], function_document["returns"])
    signatures[function_name] = (
      tuple(
        cast(str, argument["name"])
        for argument in arguments
        if argument["name"] is not None
      ),
      tuple(
        _database_type_name(cast(dict[str, object], argument["type"]))
        for argument in arguments
      ),
      _database_type_name(returns),
      cast(bool, function_document["returns_set"]),
      volatility_codes[cast(str, function_document["volatility"])],
    )
  return signatures


def _type_document(column_type: TypeEngine[Any]) -> dict[str, object]:
  if isinstance(column_type, sqlalchemy.TypeDecorator):
    return _type_document(cast(TypeEngine[Any], column_type.impl))
  if isinstance(column_type, ARRAY):
    return {
      "kind": "array",
      "items": _type_document(column_type.item_type),
    }
  if isinstance(column_type, sqlalchemy.Enum):
    return {
      "kind": "enum",
      "values": list(column_type.enums),
    }
  if isinstance(column_type, sqlalchemy.JSON):
    return {"kind": "json"}
  if isinstance(column_type, sqlalchemy.Uuid):
    return {"kind": "string", "format": "uuid"}
  if isinstance(column_type, sqlalchemy.DateTime):
    return {"kind": "string", "format": "date-time"}
  if isinstance(column_type, sqlalchemy.LargeBinary):
    return {"kind": "string", "format": "bytea"}
  if isinstance(column_type, TSVECTOR):
    return {"kind": "string", "format": "tsvector"}
  if isinstance(column_type, sqlalchemy.Boolean):
    return {"kind": "boolean"}
  if isinstance(
    column_type,
    (sqlalchemy.Integer, sqlalchemy.Float, sqlalchemy.Numeric),
  ):
    return {"kind": "number"}
  if isinstance(column_type, (sqlalchemy.String, sqlalchemy.Text)):
    return {"kind": "string"}
  if column_type.__class__.__name__ == "VECTOR":
    return {
      "kind": "array",
      "items": {"kind": "number"},
    }
  raise TypeError(f"unsupported protocol column type: {column_type!r}")


def _column_document(column: Column[Any]) -> dict[str, object]:
  generated = column.primary_key and isinstance(column.type, sqlalchemy.Integer)
  return {
    "type": _type_document(column.type),
    "nullable": column.nullable,
    "generated": generated,
    "has_default": (
      generated or column.default is not None or column.server_default is not None
    ),
  }


def _relationship_document(
  table: Table,
  constraint: ForeignKeyConstraint,
) -> dict[str, object]:
  elements = sorted(constraint.elements, key=lambda element: element.parent.name)
  columns = [element.parent.name for element in elements]
  referenced_columns = [element.column.name for element in elements]
  referenced_tables = {element.column.table.name for element in elements}
  if len(referenced_tables) != 1:
    raise ValueError(f"{table.fullname} contains a foreign key spanning multiple relations")

  foreign_key_name = constraint.name or f"{table.name}_{'_'.join(columns)}_fkey"
  return {
    "foreign_key_name": foreign_key_name,
    "columns": columns,
    "referenced_relation": referenced_tables.pop(),
    "referenced_columns": referenced_columns,
    "one_to_one": False,
  }


def _relation_document(table: Table) -> dict[str, object]:
  relationships = [
    _relationship_document(table, constraint)
    for constraint in table.foreign_key_constraints
  ]
  relationships.sort(
    key=lambda relationship: (
      relationship["foreign_key_name"],
      relationship["referenced_relation"],
    )
  )
  return {
    "columns": {
      column.name: _column_document(column)
      for column in sorted(table.columns, key=lambda column: column.name)
    },
    "relationships": relationships,
  }


def protocol_document() -> dict[str, object]:
  """Describe every relation admitted through PostgREST without opening a database."""
  metadata = get_target_metadata()
  relations = {
    table.name: _relation_document(table)
    for table in sorted(metadata.tables.values(), key=lambda table: table.name)
    if table.schema == PROTOCOL_SCHEMA
  }
  return {
    "format": PROTOCOL_DOCUMENT_FORMAT,
    "schema": PROTOCOL_SCHEMA,
    "relations": relations,
    "functions": PROTOCOL_FUNCTIONS,
  }
