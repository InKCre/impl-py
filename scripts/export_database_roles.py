"""Export password-free role definitions required to restore the schema artifact."""

from pathlib import Path
import sys

from psycopg import sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database_contract.connection import database_connection
from inkcre_core.database_contract.constants import (
  ANONYMOUS_ROLE,
  AUTHENTICATED_ROLE,
  AUTHENTICATOR_ROLE,
  CORE_RUNTIME_ROLE,
)


ROLE_NAMES = tuple(
  sorted(
    (
      ANONYMOUS_ROLE,
      AUTHENTICATED_ROLE,
      AUTHENTICATOR_ROLE,
      CORE_RUNTIME_ROLE,
    )
  )
)


def _attribute_clauses(attributes: tuple[bool, ...]) -> tuple[str, ...]:
  login, inherit, superuser, create_database, create_role, replication, bypass_rls = (
    attributes
  )
  elevated_attributes = (
    superuser,
    create_database,
    create_role,
    replication,
    bypass_rls,
  )
  if any(elevated_attributes):
    raise ValueError("database contract roles must not have elevated attributes")
  return (
    "LOGIN" if login else "NOLOGIN",
    "INHERIT" if inherit else "NOINHERIT",
    "NOSUPERUSER",
    "NOCREATEDB",
    "NOCREATEROLE",
    "NOREPLICATION",
    "NOBYPASSRLS",
  )


def export_role_statements() -> tuple[str, ...]:
  with database_connection() as connection, connection.cursor() as cursor:
    cursor.execute(
      """
      SELECT
        rolname,
        rolcanlogin,
        rolinherit,
        rolsuper,
        rolcreatedb,
        rolcreaterole,
        rolreplication,
        rolbypassrls
      FROM pg_roles
      WHERE rolname = ANY(%s)
      ORDER BY rolname
      """,
      (list(ROLE_NAMES),),
    )
    rows = cursor.fetchall()
    if tuple(row[0] for row in rows) != ROLE_NAMES:
      raise ValueError("database contract roles are incomplete")

    statements = []
    for role_name, *attributes in rows:
      identifier = sql.Identifier(role_name).as_string(connection)
      clauses = " ".join(_attribute_clauses(tuple(attributes)))
      statements.append(f"CREATE ROLE {identifier} WITH {clauses};")
    return tuple(statements)


def main() -> None:
  print("-- Password-free principals required by the InKCre database schema.")
  print(*export_role_statements(), sep="\n")


if __name__ == "__main__":
  main()
