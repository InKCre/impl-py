"""Deterministic catalog, seed, and development reset operations."""

from psycopg import sql
from psycopg.types.json import Jsonb

from .connection import database_connection
from .constants import (
  APPLICATION_TABLES,
  CONTRACT_REVISION,
  DATABASE_ENVIRONMENTS,
  DEVELOPMENT_PEER_ID,
  DEVELOPMENT_PEER_NAME,
  INTERNAL_SCHEMA,
  PROTOCOL_SCHEMA,
)
from .profile import (
  BUILTIN_AI_DIALECTS,
  BUILTIN_JOB_TYPES,
  BUILTIN_STORAGES,
  BUILTIN_STORAGE_TYPES,
)


def configure_environment(
  environment: str,
  database_url: str | None = None,
) -> None:
  """Set an immutable database environment identity after migration."""
  if environment not in DATABASE_ENVIRONMENTS:
    raise ValueError(f"unsupported database environment: {environment}")

  with database_connection(database_url) as connection:
    with connection.cursor() as cursor:
      cursor.execute(
        sql.SQL(
          "SELECT environment FROM {}.contract_state WHERE singleton FOR UPDATE"
        ).format(sql.Identifier(INTERNAL_SCHEMA))
      )
      row = cursor.fetchone()
      if row is None:
        raise RuntimeError("database contract state is not initialized")
      current = row[0]
      if current not in {"runtime", environment}:
        raise ValueError(
          f"database environment is immutable: {current} cannot become {environment}"
        )
      cursor.execute(
        sql.SQL(
          "UPDATE {}.contract_state "
          "SET environment = %s, contract_revision = %s, "
          "updated_at = CURRENT_TIMESTAMP "
          "WHERE singleton"
        ).format(sql.Identifier(INTERNAL_SCHEMA)),
        (environment, CONTRACT_REVISION),
      )


def reconcile_builtins(database_url: str | None = None) -> None:
  """Upsert artifact-owned catalogs without starting any runtime service."""
  with database_connection(database_url) as connection:
    with connection.cursor() as cursor:
      for dialect in BUILTIN_AI_DIALECTS:
        cursor.execute(
          sql.SQL(
            """
            INSERT INTO {}.ai_dialects (id, description, config_schema)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET description = EXCLUDED.description,
                config_schema = EXCLUDED.config_schema
            """
          ).format(sql.Identifier(PROTOCOL_SCHEMA)),
          (dialect.id, dialect.description, Jsonb(dialect.config_schema)),
        )

      for storage_type in BUILTIN_STORAGE_TYPES:
        cursor.execute(
          sql.SQL(
            """
            INSERT INTO {}.storage_types (id, description, config_schema, writable)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET description = EXCLUDED.description,
                config_schema = EXCLUDED.config_schema,
                writable = EXCLUDED.writable
            """
          ).format(sql.Identifier(PROTOCOL_SCHEMA)),
          (
            storage_type.id,
            storage_type.description,
            Jsonb(storage_type.config_schema),
            storage_type.writable,
          ),
        )

      for job_type in BUILTIN_JOB_TYPES:
        cursor.execute(
          sql.SQL(
            """
            INSERT INTO {}.job_types (
              id, description, parameters_schema, default_timeout_seconds
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET description = EXCLUDED.description,
                parameters_schema = EXCLUDED.parameters_schema,
                default_timeout_seconds = EXCLUDED.default_timeout_seconds
            """
          ).format(sql.Identifier(PROTOCOL_SCHEMA)),
          (
            job_type.id,
            job_type.description,
            Jsonb(job_type.parameters_schema),
            job_type.default_timeout_seconds,
          ),
        )

      for storage in BUILTIN_STORAGES:
        cursor.execute(
          sql.SQL(
            """
            INSERT INTO {}.storages (id, type, nickname, config)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
            SET type = EXCLUDED.type,
                nickname = EXCLUDED.nickname,
                config = EXCLUDED.config
            """
          ).format(sql.Identifier(PROTOCOL_SCHEMA)),
          (storage.id, storage.type, storage.nickname, Jsonb(storage.config)),
        )


def _require_development(cursor) -> None:
  cursor.execute(
    sql.SQL("SELECT environment FROM {}.contract_state WHERE singleton").format(
      sql.Identifier(INTERNAL_SCHEMA)
    )
  )
  row = cursor.fetchone()
  if row is None or row[0] != "development":
    raise ValueError("operation requires a development database identity")


def seed_development(database_url: str | None = None) -> None:
  """Upsert the minimum stable development/E2E Peer baseline."""
  with database_connection(database_url) as connection:
    with connection.cursor() as cursor:
      _require_development(cursor)
      cursor.execute(
        sql.SQL(
          """
          INSERT INTO {}.peers (
            id,
            name,
            labels,
            config,
            config_schema,
            capabilities,
            lease_expires_at,
            created_at
          )
          VALUES (
            %s,
            %s,
            ARRAY['development', 'canonical-seed']::text[],
            jsonb_build_object(),
            jsonb_build_object(),
            '[]'::jsonb,
            NULL,
            '2000-01-01T00:00:00Z'::timestamptz
          )
          ON CONFLICT (id) DO UPDATE
          SET name = EXCLUDED.name,
              labels = EXCLUDED.labels,
              config = EXCLUDED.config,
              config_schema = EXCLUDED.config_schema,
              capabilities = EXCLUDED.capabilities,
              lease_expires_at = EXCLUDED.lease_expires_at,
              created_at = EXCLUDED.created_at
          """
        ).format(sql.Identifier(PROTOCOL_SCHEMA)),
        (DEVELOPMENT_PEER_ID, DEVELOPMENT_PEER_NAME),
      )


def truncate_development(database_url: str | None = None) -> None:
  """Delete application rows only after proving the database is development."""
  with database_connection(database_url) as connection:
    with connection.cursor() as cursor:
      _require_development(cursor)
      relations = sql.SQL(", ").join(
        sql.SQL("{}.{}").format(
          sql.Identifier(PROTOCOL_SCHEMA),
          sql.Identifier(table_name),
        )
        for table_name in APPLICATION_TABLES
      )
      cursor.execute(
        sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(relations)
      )
