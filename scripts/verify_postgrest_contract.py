"""Black-box acceptance probe for the authenticated PostgREST peer contract."""

import argparse
import json
from pathlib import Path
import sys
import time
from urllib import error, parse, request
import uuid

import jwt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from inkcre_core.database_contract.constants import (
  JWT_ALGORITHM,
  JWT_AUDIENCE,
  JWT_ISSUER,
  JWT_ROLE,
)


PROBE_PEER_ID = uuid.UUID("00000000-0000-4000-8000-000000000099")
PROBE_EXTENSION_NAME = "inkcre/postgrest-contract-probe"


def _token(secret: str, *, now: int | None = None) -> str:
  issued_at = now if now is not None else int(time.time())
  return jwt.encode(
    {
      "role": JWT_ROLE,
      "iss": JWT_ISSUER,
      "aud": JWT_AUDIENCE,
      "iat": issued_at,
      "exp": issued_at + 600,
    },
    secret,
    algorithm=JWT_ALGORITHM,
  )


def _call(
  base_url: str,
  path: str,
  *,
  method: str = "GET",
  token: str | None = None,
  document: object | None = None,
) -> tuple[int, bytes]:
  headers = {"Accept": "application/json"}
  if token is not None:
    headers["Authorization"] = f"Bearer {token}"
  body = None
  if document is not None:
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=representation"
    body = json.dumps(document).encode()
  http_request = request.Request(
    f"{base_url.rstrip('/')}/{path.lstrip('/')}",
    data=body,
    headers=headers,
    method=method,
  )
  try:
    with request.urlopen(http_request, timeout=10) as response:
      return response.status, response.read()
  except error.HTTPError as exc:
    return exc.code, exc.read()


def _expect(actual: int, expected: int, name: str) -> None:
  if actual != expected:
    raise RuntimeError(f"{name} returned HTTP {actual}, expected {expected}")


def verify(base_url: str, secret: str, wrong_secret: str) -> dict[str, object]:
  """Prove representative read/write and rejection semantics."""
  valid_token = _token(secret)
  invalid_token = _token(wrong_secret)
  escaped_id = parse.quote(str(PROBE_PEER_ID), safe="")

  _call(
    base_url,
    f"peers?id=eq.{escaped_id}",
    method="DELETE",
    token=valid_token,
  )

  checks: dict[str, int] = {}
  checks["authenticated_read"], _ = _call(
    base_url,
    "peers?select=id&limit=1",
    token=valid_token,
  )
  _expect(checks["authenticated_read"], 200, "authenticated read")

  checks["authenticated_write"], response_body = _call(
    base_url,
    "peers",
    method="POST",
    token=valid_token,
    document={
      "id": str(PROBE_PEER_ID),
      "name": "postgrest-contract-probe",
      "labels": ["contract-probe"],
      "config": {},
      "config_schema": {},
    },
  )
  _expect(checks["authenticated_write"], 201, "authenticated write")
  created = json.loads(response_body)
  if not created or created[0].get("id") != str(PROBE_PEER_ID):
    raise RuntimeError("authenticated write returned an unexpected record")

  escaped_extension = parse.quote(PROBE_EXTENSION_NAME, safe="")
  _call(
    base_url,
    f"extensions?name=eq.{escaped_extension}",
    method="DELETE",
    token=valid_token,
  )
  checks["extension_insert"], _ = _call(
    base_url,
    "extensions",
    method="POST",
    token=valid_token,
    document={
      "name": PROBE_EXTENSION_NAME,
      "config": {},
    },
  )
  _expect(checks["extension_insert"], 201, "Extension insert")
  checks["shared_config_update"], _ = _call(
    base_url,
    f"extensions?name=eq.{escaped_extension}",
    method="PATCH",
    token=valid_token,
    document={"config": {"probe": True}},
  )
  _expect(checks["shared_config_update"], 200, "shared config update")
  checks["shared_state_update"], _ = _call(
    base_url,
    f"extensions?name=eq.{escaped_extension}",
    method="PATCH",
    token=valid_token,
    document={"state": {"shared": True}},
  )
  _expect(checks["shared_state_update"], 200, "shared state update")
  checks["shared_state_read"], state_body = _call(
    base_url,
    f"extensions?name=eq.{escaped_extension}&select=state",
    token=valid_token,
  )
  _expect(checks["shared_state_read"], 200, "shared state read")
  state_rows = json.loads(state_body)
  if not state_rows or state_rows[0].get("state") != {"shared": True}:
    raise RuntimeError("shared state read returned an unexpected value")
  checks["non_object_state_rejected"], _ = _call(
    base_url,
    f"extensions?name=eq.{escaped_extension}",
    method="PATCH",
    token=valid_token,
    document={"state": []},
  )
  _expect(checks["non_object_state_rejected"], 400, "non-object state update")
  checks["identity_update_denied"], _ = _call(
    base_url,
    f"extensions?name=eq.{escaped_extension}",
    method="PATCH",
    token=valid_token,
    document={"name": "inkcre/renamed-probe"},
  )
  _expect(checks["identity_update_denied"], 403, "Extension identity update")
  checks["anonymous_state_write_denied"], _ = _call(
    base_url,
    f"extensions?name=eq.{escaped_extension}",
    method="PATCH",
    document={"state": {"unauthorized": True}},
  )
  _expect(checks["anonymous_state_write_denied"], 401, "anonymous state write")
  checks["removed_legacy_selection_fields"], _ = _call(
    base_url,
    f"extensions?name=eq.{escaped_extension}",
    method="PATCH",
    token=valid_token,
    document={"version": "2.0.0"},
  )
  if checks["removed_legacy_selection_fields"] not in (400, 403):
    raise RuntimeError(
      "legacy Extension version field was not rejected: "
      f"HTTP {checks['removed_legacy_selection_fields']}"
    )
  checks["participation_insert"], participation_body = _call(
    base_url,
    "extension_peer_participations",
    method="POST",
    token=valid_token,
    document={
      "name": PROBE_EXTENSION_NAME,
      "peer_id": str(PROBE_PEER_ID),
      "distribution": "python",
      "version": "1.0.0",
      "enabled": True,
    },
  )
  _expect(checks["participation_insert"], 201, "Peer participation insert")
  participation_rows = json.loads(participation_body)
  if (
    not participation_rows
    or participation_rows[0].get("peer_id") != str(PROBE_PEER_ID)
    or participation_rows[0].get("distribution") != "python"
    or participation_rows[0].get("version") != "1.0.0"
    or participation_rows[0].get("enabled") is not True
  ):
    raise RuntimeError("participation insert returned an unexpected record")
  checks["legacy_enabled_rpc_removed"], _ = _call(
    base_url,
    "rpc/set_extension_peer_enabled",
    method="POST",
    token=valid_token,
    document={
      "p_name": PROBE_EXTENSION_NAME,
      "p_peer_id": str(PROBE_PEER_ID),
      "p_enabled": True,
    },
  )
  _expect(checks["legacy_enabled_rpc_removed"], 404, "removed enabled RPC")
  checks["participation_cleanup"], _ = _call(
    base_url,
    f"extension_peer_participations?name=eq.{escaped_extension}&peer_id=eq.{escaped_id}",
    method="DELETE",
    token=valid_token,
  )
  _expect(checks["participation_cleanup"], 204, "participation cleanup")
  checks["extension_cleanup"], _ = _call(
    base_url,
    f"extensions?name=eq.{escaped_extension}",
    method="DELETE",
    token=valid_token,
  )
  _expect(checks["extension_cleanup"], 204, "Extension cleanup")

  checks["wrong_secret"], _ = _call(
    base_url,
    "peers?select=id&limit=1",
    token=invalid_token,
  )
  _expect(checks["wrong_secret"], 401, "wrong secret")

  checks["anonymous"], _ = _call(
    base_url,
    "peers?select=id&limit=1",
  )
  _expect(checks["anonymous"], 401, "anonymous request")

  checks["cleanup"], _ = _call(
    base_url,
    f"peers?id=eq.{escaped_id}",
    method="DELETE",
    token=valid_token,
  )
  _expect(checks["cleanup"], 204, "probe cleanup")

  return {
    "status": "ok",
    "surface": "postgrest",
    "checks": checks,
  }


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser()
  parser.add_argument("--base-url", required=True)
  parser.add_argument("--jwt-secret", required=True)
  parser.add_argument("--wrong-jwt-secret", required=True)
  return parser


def main() -> int:
  args = build_parser().parse_args()
  try:
    result = verify(
      args.base_url,
      args.jwt_secret,
      args.wrong_jwt_secret,
    )
  except Exception:
    print(
      json.dumps(
        {
          "status": "error",
          "surface": "postgrest",
          "reason": "acceptance_failed",
        },
        sort_keys=True,
      )
    )
    return 1
  print(json.dumps(result, sort_keys=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
