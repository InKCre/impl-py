"""Ordered lifecycle operations over independently callable primitives."""

import os

from .catalog import (
  configure_environment,
  reconcile_builtins,
  seed_development,
  truncate_development,
)
from .constants import (
  CONTRACT_FORMAT,
  CONTRACT_REVISION,
  JWT_ALGORITHM,
  JWT_AUDIENCE,
  JWT_ISSUER,
  JWT_ROLE,
  RESET_CONFIRMATION,
)
from .migration import get_repository_heads, migrate
from .protocol import protocol_document
from .roles import RoleSecrets, provision_roles


def initialize(
  profile: str,
  *,
  environment: str | None = None,
  secrets: RoleSecrets | None = None,
) -> None:
  """Run the sole supported initialization order."""
  if profile not in {"runtime", "development"}:
    raise ValueError(f"unsupported initialization profile: {profile}")
  selected_environment = (
    "development"
    if profile == "development"
    else environment or os.getenv("INKCRE_DATABASE_ENVIRONMENT", "runtime")
  )

  migrate()
  configure_environment(selected_environment)
  provision_roles(secrets or RoleSecrets.from_environment())
  reconcile_builtins()
  if profile == "development":
    seed_development()


def reset_development(
  confirmation: str,
  *,
  secrets: RoleSecrets | None = None,
) -> None:
  """Restore the deterministic development baseline after a double guard."""
  if confirmation != RESET_CONFIRMATION:
    raise ValueError(f"confirmation must be exactly {RESET_CONFIRMATION}")

  truncate_development()
  migrate()
  provision_roles(secrets or RoleSecrets.from_environment())
  reconcile_builtins()
  seed_development()


def contract_document() -> dict[str, object]:
  """Return stable, non-secret contract metadata."""
  source_revision = os.getenv(
    "INKCRE_SOURCE_REVISION",
    os.getenv("HEROKU_SLUG_COMMIT", "unknown"),
  )
  return {
    "format": CONTRACT_FORMAT,
    "revision": CONTRACT_REVISION,
    "migration_heads": list(get_repository_heads()),
    "source_revision": source_revision,
    "jwt": {
      "algorithm": JWT_ALGORITHM,
      "role": JWT_ROLE,
      "issuer": JWT_ISSUER,
      "audience": JWT_AUDIENCE,
      "required_claims": ["role", "iss", "aud", "iat", "exp"],
    },
    "profiles": ["runtime", "development"],
    "protocol": protocol_document(),
    "commands": [
      "init",
      "migrate",
      "provision-roles",
      "reconcile-builtins",
      "seed-dev",
      "ready",
      "reset-dev",
      "contract",
      "schema",
    ],
  }
