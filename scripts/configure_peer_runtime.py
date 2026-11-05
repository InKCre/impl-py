"""Converge one deployed core Peer's database-owned HTTP advertisement config."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time
import typing
import uuid

from psycopg.types.json import Jsonb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database_contract.connection import database_connection
from inkcre_core.schemas.peer import CorePeerConfig, PEER_HTTP_PROTOCOL


CAPABILITY_PATHS = {
  "core.extension.management.v2": "/extension-management/v2",
  "core.feature_retrieval.lexical.v1": "/lexical-retrieval",
  "core.organization.rumination.v1": "/organization/ruminate",
  "core.semantic_retrieval.v1": "/semantic-retrieval",
}


def expected_capability_snapshot(base_url: str) -> list[dict[str, typing.Any]]:
  config = CorePeerConfig(http_public_base_url=base_url)
  normalized = config.http_public_base_url
  if normalized is None:  # pragma: no cover - non-null caller invariant
    raise ValueError("deployed Peer HTTP base URL is required")
  return [
    {
      "id": capability,
      "inbound": {
        "protocol": PEER_HTTP_PROTOCOL,
        "parameters": {
          "method": "POST",
          "url": f"{normalized}{path}",
        },
      },
    }
    for capability, path in sorted(CAPABILITY_PATHS.items())
  ]


def snapshot_is_ready(
  capabilities: typing.Any,
  lease_is_live: bool,
  expected: list[dict[str, typing.Any]],
) -> bool:
  if not lease_is_live or not isinstance(capabilities, list):
    return False
  published = {
    capability.get("id"): capability
    for capability in capabilities
    if isinstance(capability, dict) and isinstance(capability.get("id"), str)
  }
  return all(published.get(capability["id"]) == capability for capability in expected)


def configure_peer_runtime(
  database_url: str,
  peer_id: uuid.UUID,
  http_public_base_url: str,
  *,
  wait_seconds: float,
) -> None:
  expected = expected_capability_snapshot(http_public_base_url)
  normalized_url = typing.cast(
    str,
    CorePeerConfig(http_public_base_url=http_public_base_url).http_public_base_url,
  )
  deadline = time.monotonic() + wait_seconds
  with database_connection(database_url) as connection:
    while True:
      with connection.cursor() as cursor:
        cursor.execute(
          "SELECT config FROM inkcre.peers WHERE id = %s FOR UPDATE",
          (peer_id,),
        )
        row = cursor.fetchone()
        if row is not None:
          current = CorePeerConfig.model_validate(row[0] or {})
          next_config = current.model_copy(
            update={"http_public_base_url": normalized_url}
          ).model_dump(mode="json")
          cursor.execute(
            "UPDATE inkcre.peers SET config = %s WHERE id = %s",
            (Jsonb(next_config), peer_id),
          )
          break
      connection.rollback()
      if time.monotonic() >= deadline:
        raise RuntimeError(f"Peer {peer_id} did not register before convergence")
      time.sleep(1)
    connection.commit()

    while True:
      with connection.cursor() as cursor:
        cursor.execute(
          "SELECT capabilities, "
          "lease_expires_at > statement_timestamp() "
          "FROM inkcre.peers WHERE id = %s",
          (peer_id,),
        )
        row = cursor.fetchone()
      if row is None:
        raise RuntimeError(f"Peer {peer_id} disappeared during convergence")
      if snapshot_is_ready(row[0], bool(row[1]), expected):
        return
      if time.monotonic() >= deadline:
        raise RuntimeError(
          f"Peer {peer_id} did not publish its exact live capability snapshot"
        )
      time.sleep(1)


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser()
  parser.add_argument("--peer-id", type=uuid.UUID, required=True)
  parser.add_argument("--http-public-base-url", required=True)
  parser.add_argument("--wait-seconds", type=float, default=90)
  return parser


def main() -> int:
  args = build_parser().parse_args()
  database_url = os.getenv("DATABASE_URL", "")
  if not database_url:
    raise SystemExit("DATABASE_URL is required")
  if args.wait_seconds < 0:
    raise SystemExit("--wait-seconds cannot be negative")
  configure_peer_runtime(
    database_url,
    args.peer_id,
    args.http_public_base_url,
    wait_seconds=args.wait_seconds,
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
