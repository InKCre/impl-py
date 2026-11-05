"""Read-only production database inventory for the protected preflight workflow."""

import datetime
import json
import os
import sys
import uuid
from urllib.parse import unquote, urlsplit

from app.database_contract.connection import database_connection
from inkcre_core.database_contract.constants import (
  ANONYMOUS_ROLE,
  AUTHENTICATED_ROLE,
  AUTHENTICATOR_ROLE,
  CORE_RUNTIME_ROLE,
  CONTRACT_REVISION,
  INTERNAL_SCHEMA,
  PROTOCOL_SCHEMA,
)
from app.database_contract.migration import get_repository_heads


def _json_value(value):
  if isinstance(value, (datetime.date, datetime.datetime)):
    return value.isoformat()
  return value


def _query(cursor, statement: str, parameters=()) -> list[tuple]:
  cursor.execute(statement, parameters)
  return list(cursor.fetchall())


def _database_identity(value: str) -> tuple[str, int | None, str, str]:
  parsed = urlsplit(value)
  if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
    raise ValueError("database URL scheme is not PostgreSQL")
  if not parsed.hostname or not parsed.path.strip("/") or not parsed.username:
    raise ValueError("database URL identity is incomplete")
  return (
    parsed.hostname.lower(),
    parsed.port or 5432,
    unquote(parsed.path.strip("/")),
    unquote(parsed.username),
  )


def verify_app_configuration(
  owner_database_url: str,
  core_config_json: str,
  postgrest_config_json: str,
  core_app_name: str,
) -> dict:
  """Compare configured database identities without returning secret values."""
  core = json.loads(core_config_json)
  postgrest = json.loads(postgrest_config_json)
  for config, required in (
    (core, ("DATABASE_URL", "JWT_SECRET", "PEER_ID")),
    (postgrest, ("PGRST_DB_URI", "PGRST_JWT_SECRET")),
  ):
    if any(not isinstance(config.get(key), str) or not config[key] for key in required):
      raise ValueError("required production configuration is missing")

  owner = _database_identity(owner_database_url)
  core_identity = _database_identity(core["DATABASE_URL"])
  postgrest_identity = _database_identity(postgrest["PGRST_DB_URI"])
  core_matches = core_identity[:3] == owner[:3] and core_identity[3] == CORE_RUNTIME_ROLE
  postgrest_matches = (
    postgrest_identity[:3] == owner[:3] and postgrest_identity[3] == AUTHENTICATOR_ROLE
  )
  jwt_matches = core["JWT_SECRET"] == postgrest["PGRST_JWT_SECRET"]
  peer_id = str(uuid.UUID(core["PEER_ID"]))
  expected_peer_id = str(uuid.uuid5(uuid.NAMESPACE_URL, core_app_name))
  if (
    not core_matches
    or not postgrest_matches
    or not jwt_matches
    or peer_id != expected_peer_id
  ):
    raise ValueError(
      "production app configuration does not match the selected deployment identity"
    )
  return {
    "core_database_matches_neon_branch": core_matches,
    "postgrest_database_matches_neon_branch": postgrest_matches,
    "core_database_role": core_identity[3],
    "postgrest_database_role": postgrest_identity[3],
    "jwt_secrets_match": jwt_matches,
    "core_peer_id": peer_id,
    "expected_core_peer_id": expected_peer_id,
  }


def checked_in_production_profile(profile_json: str) -> dict:
  """Return the non-secret profile declarations from the checked-in source."""
  profile = json.loads(profile_json)
  return {
    "environment": profile["environment"],
    "contract_revision": profile["database_contract"]["revision"],
    "migration_head": profile["database_contract"]["migration_head"],
    "core_peer_id": profile["core"]["peer_id"],
    "core_url": profile["core"]["url"],
    "postgrest_url": profile["postgrest"]["url"],
  }


def collect_database_inventory(connection, core_peer_id: uuid.UUID) -> dict:
  """Take one transactionally consistent, read-only operational snapshot."""
  with connection.transaction():
    cursor = connection.cursor()
    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

    current_migrations = [
      row[0]
      for row in _query(
        cursor,
        "SELECT version_num FROM alembic_version ORDER BY version_num",
      )
    ]
    contract_rows = _query(
      cursor,
      f"""
      SELECT contract_revision, environment, owner_role
      FROM {INTERNAL_SCHEMA}.contract_state
      WHERE singleton
      """,
    )
    if len(contract_rows) != 1:
      raise RuntimeError("database contract state is missing or ambiguous")
    contract_revision, environment, owner_role = contract_rows[0]

    peer_rows = _query(
      cursor,
      f"""
      SELECT id::text, name, application_version, lease_expires_at,
             COALESCE(lease_expires_at > transaction_timestamp(), false)
      FROM {PROTOCOL_SCHEMA}.peers
      ORDER BY id
      """,
    )
    peers = [
      {
        "id": row[0],
        "name": row[1],
        "application_version": row[2],
        "lease_expires_at": _json_value(row[3]),
        "lease_active": row[4],
      }
      for row in peer_rows
    ]
    if not any(row["id"] == str(core_peer_id) for row in peers):
      raise RuntimeError("configured Core Peer ID is not registered in the database")

    has_participations = _query(
      cursor,
      "SELECT to_regclass(%s) IS NOT NULL",
      (f"{PROTOCOL_SCHEMA}.extension_peer_participations",),
    )[0][0]
    if has_participations:
      selection_rows = _query(
        cursor,
        f"""
        SELECT name, peer_id::text, distribution, version, enabled
        FROM {PROTOCOL_SCHEMA}.extension_peer_participations
        ORDER BY peer_id, name
        """,
      )
      selection_source = "peer_participations"
    else:
      selection_rows = _query(
        cursor,
        f"""
        SELECT extension.name, peer_id::text, NULL::text, extension.version, true
        FROM {PROTOCOL_SCHEMA}.extensions AS extension
        CROSS JOIN LATERAL unnest(extension.enabled) AS peer_id
        ORDER BY peer_id, extension.name
        """,
      )
      selection_source = "legacy_extension_snapshot"
    selections = [
      {
        "name": row[0],
        "peer_id": row[1],
        "distribution": row[2],
        "version": row[3],
        "enabled": row[4],
      }
      for row in selection_rows
    ]

    active_jobs = [
      {
        "id": row[0],
        "type": row[1],
        "status": row[2],
        "created_at": _json_value(row[3]),
        "started_at": _json_value(row[4]),
        "abort_requested": row[5],
      }
      for row in _query(
        cursor,
        f"""
        SELECT id, type, status::text, created_at, started_at, abort_requested
        FROM {PROTOCOL_SCHEMA}.jobs
        WHERE status IN ('pending', 'running')
        ORDER BY id
        """,
      )
    ]

    role_names = list(
      dict.fromkeys(
        [
          ANONYMOUS_ROLE,
          AUTHENTICATED_ROLE,
          AUTHENTICATOR_ROLE,
          CORE_RUNTIME_ROLE,
          owner_role,
        ]
      )
    )
    roles = [
      {
        "name": row[0],
        "can_login": row[1],
        "inherits": row[2],
        "superuser": row[3],
        "create_database": row[4],
        "create_role": row[5],
        "replication": row[6],
        "bypass_rls": row[7],
      }
      for row in _query(
        cursor,
        """
        SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb,
               rolcreaterole, rolreplication, rolbypassrls
        FROM pg_roles
        WHERE rolname = ANY(%s)
        ORDER BY rolname
        """,
        (role_names,),
      )
    ]
    role_memberships = [
      {"member": row[0], "granted_role": row[1], "admin_option": row[2]}
      for row in _query(
        cursor,
        """
        SELECT member.rolname, granted.rolname, membership.admin_option
        FROM pg_auth_members AS membership
        JOIN pg_roles AS member ON member.oid = membership.member
        JOIN pg_roles AS granted ON granted.oid = membership.roleid
        WHERE member.rolname = ANY(%s) OR granted.rolname = ANY(%s)
        ORDER BY member.rolname, granted.rolname
        """,
        (role_names, role_names),
      )
    ]
    table_privileges = [
      {
        "role": row[0],
        "table": row[1],
        "schema_usage": row[2],
        "select": row[3],
        "insert": row[4],
        "update": row[5],
        "delete": row[6],
      }
      for row in _query(
        cursor,
        f"""
        SELECT role.rolname, table_.relname,
               has_schema_privilege(role.oid, namespace.oid, 'USAGE'),
               has_table_privilege(role.oid, table_.oid, 'SELECT'),
               has_table_privilege(role.oid, table_.oid, 'INSERT'),
               has_table_privilege(role.oid, table_.oid, 'UPDATE'),
               has_table_privilege(role.oid, table_.oid, 'DELETE')
        FROM pg_roles AS role
        CROSS JOIN pg_namespace AS namespace
        JOIN pg_class AS table_ ON table_.relnamespace = namespace.oid
        WHERE role.rolname = ANY(%s)
          AND namespace.nspname = %s
          AND table_.relkind IN ('r', 'p')
          AND table_.relname = ANY(%s)
        ORDER BY role.rolname, table_.relname
        """,
        (
          role_names,
          PROTOCOL_SCHEMA,
          ["peers", "extensions", "extension_peer_participations", "jobs"],
        ),
      )
    ]

    sessions = [
      {
        "user": row[0],
        "application": row[1],
        "backend_type": row[2],
        "state": row[3],
        "transaction_started_at": _json_value(row[4]),
      }
      for row in _query(
        cursor,
        """
        SELECT usename, application_name, backend_type, state, xact_start
        FROM pg_stat_activity
        WHERE datname = current_database() AND pid <> pg_backend_pid()
        ORDER BY usename, application_name, backend_type
        """,
      )
    ]

    return {
      "snapshot_at": datetime.datetime.now(datetime.UTC).isoformat(),
      "current_migration_heads": current_migrations,
      "candidate_migration_heads": list(get_repository_heads()),
      "database_contract": {
        "current_revision": contract_revision,
        "candidate_revision": CONTRACT_REVISION,
        "environment": environment,
        "owner_role": owner_role,
      },
      "core_peer_id": str(core_peer_id),
      "peers": peers,
      "extension_selection_source": selection_source,
      "extension_selections": selections,
      "active_jobs": active_jobs,
      "roles": roles,
      "role_memberships": role_memberships,
      "table_privileges": table_privileges,
      "database_sessions": sessions,
      "observation_limit": (
        "This is one read-only database snapshot. It does not prove that every external "
        "Peer or direct database writer has stopped."
      ),
    }


def quiescence_conflicts(inventory: dict, core_peer_id: uuid.UUID) -> list[str]:
  """Return observed work that blocks a maintenance migration after app shutdown."""
  conflicts = []
  external_peers = [
    peer
    for peer in inventory["peers"]
    if peer["id"] != str(core_peer_id) and peer["lease_active"]
  ]
  if external_peers:
    conflicts.append("another Peer still has an active database lease")
  if any(job["status"] == "running" for job in inventory["active_jobs"]):
    conflicts.append("a database Job is still running")
  idle_sessions = all(
    session.get("backend_type") == "client backend"
    and session.get("state") == "idle"
    and session.get("transaction_started_at") is None
    for session in inventory["database_sessions"]
  )
  if not idle_sessions:
    conflicts.append("database sessions remain after the configured apps were stopped")
  return conflicts


def read_flush_lsn(connection) -> str:
  """Return the current WAL flush position for a stopped, quiescent database."""
  with connection.transaction():
    cursor = connection.cursor()
    cursor.execute("SET TRANSACTION READ ONLY")
    cursor.execute("SELECT pg_current_wal_flush_lsn()::text")
    row = cursor.fetchone()
  if not row or not isinstance(row[0], str) or "/" not in row[0]:
    raise RuntimeError("PostgreSQL returned an invalid WAL flush LSN")
  return row[0]


def main() -> int:
  if sys.argv[1:] == ["flush-lsn"]:
    database_url = os.environ.get("MIGRATION_DATABASE_URL")
    if not database_url:
      print("MIGRATION_DATABASE_URL is required", file=sys.stderr)
      return 2
    try:
      with database_connection(database_url) as connection:
        print(read_flush_lsn(connection))
    except Exception:
      # Driver diagnostics may contain the owner URL. Keep failures credential-free.
      print("Could not read the PostgreSQL WAL flush LSN", file=sys.stderr)
      return 1
    return 0

  database_url = os.environ.get("MIGRATION_DATABASE_URL")
  app_database_identity_url = os.environ.get("APP_DATABASE_IDENTITY_URL")
  core_app_name = os.environ.get("HEROKU_APP_NAME")
  core_peer_id = os.environ.get("CORE_PEER_ID")
  core_config_json = os.environ.get("CORE_CONFIG_JSON")
  postgrest_config_json = os.environ.get("POSTGREST_CONFIG_JSON")
  production_profile_json = os.environ.get("PRODUCTION_PROFILE_JSON")
  if any(
    value is None
    for value in (
      database_url,
      app_database_identity_url,
      core_app_name,
      core_peer_id,
      core_config_json,
      postgrest_config_json,
      production_profile_json,
    )
  ):
    print("Production inventory inputs are incomplete", file=sys.stderr)
    return 2
  assert database_url and app_database_identity_url and core_app_name
  assert core_peer_id and core_config_json and postgrest_config_json
  assert production_profile_json
  try:
    parsed_peer_id = uuid.UUID(core_peer_id)
    app_configuration = verify_app_configuration(
      app_database_identity_url,
      core_config_json,
      postgrest_config_json,
      core_app_name,
    )
    if str(parsed_peer_id) != app_configuration["core_peer_id"]:
      raise ValueError("Core Peer ID does not match its application configuration")
    with database_connection(database_url) as connection:
      inventory = collect_database_inventory(connection, parsed_peer_id)
  except Exception:
    # Do not emit provider URLs or driver diagnostics that may include credentials.
    print(
      "Production database inventory failed; no mutation was attempted.",
      file=sys.stderr,
    )
    return 1

  report = {
    "source_revision": os.environ.get("HEAD_SHA", "unknown"),
    "image_digest": os.environ.get("CORE_IMAGE_DIGEST", "unknown"),
    "checked_in_production_profile": checked_in_production_profile(production_profile_json),
    "application_configuration": app_configuration,
    "inventory": inventory,
  }
  rendered = json.dumps(report, indent=2, sort_keys=True)
  print(rendered)
  summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
  if summary_path:
    with open(summary_path, "a", encoding="utf-8") as summary:
      summary.write("### Core production read-only inventory\n\n```json\n")
      summary.write(rendered)
      summary.write("\n```\n")
  if os.environ.get("REQUIRE_QUIESCENT_DATABASE") == "true":
    conflicts = quiescence_conflicts(inventory, parsed_peer_id)
    if conflicts:
      print(
        "Database is not quiescent for migration: " + "; ".join(conflicts),
        file=sys.stderr,
      )
      return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
