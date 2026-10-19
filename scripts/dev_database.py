"""Own one worktree-scoped development database runtime and its access descriptor."""

from collections.abc import Mapping
import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import argparse
import json
import jwt
import re
import shutil
import subprocess
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database_contract.constants import (
  CONTRACT_REVISION,
  JWT_ALGORITHM,
  JWT_AUDIENCE,
  JWT_ISSUER,
  JWT_ROLE,
)
from app.database_contract.readiness import get_repository_heads
from scripts.dev_database_provider import (
  DatabaseProvider,
  ProviderDiagnostics,
  available_port,
  close_database_access,
  database_access_ready,
  diagnose_database_provider,
  iter_build_context_files,
  open_database_access,
  quote_environment_value,
  resolve_database_provider,
  run_database_compose,
  same_database_provider,
)


STATE_FORMAT = 1
PROFILE_FORMAT = 1
OWNER_REPOSITORY = "InKCre/impl-py"
DEVELOPMENT_PEER_ID = "00000000-0000-4000-8000-000000000002"
DEVELOPMENT_CAPABILITIES = {
  "core.extension.management.v2": "/extension-management/v2",
  "core.organization.rumination.v1": "/organization/ruminate",
  "core.semantic_retrieval.v1": "/semantic-retrieval",
}
INSTANCE_PATTERN = re.compile(r"^[a-f0-9]{16}$")


def _stable_json(value: Any) -> str:
  return f"{json.dumps(value, indent=2, sort_keys=True)}\n"


def _validate_instance(instance: str) -> str:
  if INSTANCE_PATTERN.fullmatch(instance) is None:
    raise ValueError("database runtime identity must be one SVC worktree instance")
  return instance


def _runtime_directory(instance: str) -> Path:
  return PROJECT_ROOT / ".runtime" / "database" / _validate_instance(instance)


def _project_name(instance: str) -> str:
  return f"inkcre-core-py-{_validate_instance(instance)}"


def _local_secret(instance: str, purpose: str) -> str:
  return sha256(f"inkcre-core-py/{instance}/{purpose}".encode()).hexdigest()


def _source_revision() -> str:
  result = subprocess.run(
    ("git", "rev-parse", "HEAD"),
    cwd=PROJECT_ROOT,
    check=True,
    capture_output=True,
    text=True,
  )
  return result.stdout.strip()


def _source_fingerprint() -> str:
  digest = sha256()
  runtime_paths = [PROJECT_ROOT / "docker-compose.yml", *iter_build_context_files()]
  for path in sorted(runtime_paths):
    relative_path = path.relative_to(PROJECT_ROOT)
    digest.update(str(relative_path).encode())
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
  return digest.hexdigest()


def _refresh_artifact_state(state: dict[str, Any]) -> None:
  """Project the current worktree artifact identity into reusable runtime state."""
  source_revision = _source_revision()
  state["contract_revision"] = CONTRACT_REVISION
  state["migration_head"] = get_repository_heads()[0]
  state["source_revision"] = source_revision
  state["source_fingerprint"] = _source_fingerprint()
  state["core_image"] = f"inkcre-core-py-development:{source_revision[:12]}"


def _provider_from_state(state: Mapping[str, Any]) -> DatabaseProvider:
  provider = state.get("provider")
  if not isinstance(provider, dict):
    raise ValueError("database runtime provider is missing")
  return DatabaseProvider(
    kind=str(provider.get("kind", "")),
    target=provider.get("target"),
    docker_bin=provider.get("docker_bin"),
    forward_host=provider.get("forward_host"),
  )


def _write_runtime_files(
  state: dict[str, Any],
  credentials: dict[str, str | int],
) -> None:
  directory = _runtime_directory(str(state["identity"]))
  directory.mkdir(parents=True, exist_ok=True, mode=0o700)

  profile = {
    "format": PROFILE_FORMAT,
    "environment": "development",
    "database_contract": {
      "revision": state["contract_revision"],
      "migration_head": state["migration_head"],
      "protocol_schema": "inkcre",
    },
    "runtime": {
      "instance": state["identity"],
      "owner_repository": OWNER_REPOSITORY,
      "compose_project": state["project"],
      "docker_daemon_id": state.get("docker", {}).get("daemon_id"),
      "source_revision": state["source_revision"],
      "source_fingerprint": state["source_fingerprint"],
    },
    "core": {
      "peer_id": DEVELOPMENT_PEER_ID,
      "url": state["urls"]["core"],
    },
    "postgrest": {
      "anonymous_access": "deny",
      "database_role": "authenticator",
      "url": state["urls"]["postgrest"],
    },
    "jwt": {
      "algorithm": "HS256",
      "role": "authenticated",
      "issuer": "inkcre-peer",
      "audience": "inkcre-api",
      "required_claims": ["role", "iss", "aud", "iat", "exp"],
    },
  }
  published_ports = (
    state["local_ports"]
    if state["provider"]["kind"] == "local"
    else {"postgres": 0, "core": 0, "postgrest": 0}
  )
  environment = {
    "INKCRE_COMPOSE_PROJECT_NAME": state["project"],
    "INKCRE_BUILD_CONTEXT": (
      str(PROJECT_ROOT) if state["provider"]["kind"] == "local" else "./context"
    ),
    "INKCRE_CORE_IMAGE": state["core_image"],
    "INKCRE_SOURCE_REVISION": state["source_revision"],
    "INKCRE_REMOTE_DOCKER_BIN": state["provider"].get("docker_bin") or "docker",
    "POSTGRES_PORT": str(published_ports["postgres"]),
    "CORE_PORT": str(published_ports["core"]),
    "POSTGREST_PORT": str(published_ports["postgrest"]),
    **{name: str(value) for name, value in credentials.items() if name != "format"},
  }

  (directory / "runtime.json").write_text(_stable_json(state))
  (directory / "profile.json").write_text(_stable_json(profile))
  credential_path = directory / "credential.json"
  credential_path.write_text(_stable_json(credentials))
  credential_path.chmod(0o600)
  compose_environment = directory / "compose.env"
  compose_environment.write_text(
    "".join(
      f"{name}={quote_environment_value(value)}\n" for name, value in environment.items()
    )
  )
  compose_environment.chmod(0o600)


def _read_json(path: Path) -> dict[str, Any]:
  value = json.loads(path.read_text())
  if not isinstance(value, dict):
    raise ValueError(f"{path.name} must contain a JSON object")
  return value


def _read_state(instance: str) -> dict[str, Any]:
  state = _read_json(_runtime_directory(instance) / "runtime.json")
  if state.get("format") != STATE_FORMAT or state.get("identity") != instance:
    raise ValueError(
      f"database runtime {instance} uses obsolete or mismatched state; stop it first"
    )
  if state.get("owner_repository") != OWNER_REPOSITORY:
    raise ValueError(f"database runtime {instance} belongs to another repository")
  return state


def _new_state(
  instance: str,
  provider: DatabaseProvider,
  diagnostics: ProviderDiagnostics,
) -> tuple[dict[str, Any], dict[str, str | int]]:
  local_ports = {
    "postgres": available_port(),
    "core": available_port(),
    "postgrest": available_port(),
  }
  source_revision = _source_revision()
  state: dict[str, Any] = {
    "format": STATE_FORMAT,
    "identity": instance,
    "owner_repository": OWNER_REPOSITORY,
    "project": _project_name(instance),
    "provider": provider.as_dict(),
    "docker": diagnostics.as_dict(),
    "contract_revision": CONTRACT_REVISION,
    "migration_head": get_repository_heads()[0],
    "source_revision": source_revision,
    "source_fingerprint": _source_fingerprint(),
    "converging": False,
    "core_image": f"inkcre-core-py-development:{source_revision[:12]}",
    "profile": "development",
    "local_ports": local_ports,
    "remote_ports": None,
    "urls": {
      "core": f"http://127.0.0.1:{local_ports['core']}/",
      "postgrest": f"http://127.0.0.1:{local_ports['postgrest']}/",
    },
    "tunnel": {"control_socket": None},
  }
  credentials: dict[str, str | int] = {
    "format": 1,
    "JWT_SECRET": _local_secret(instance, "jwt"),
    "POSTGRES_PASSWORD": _local_secret(instance, "postgres"),
    "CORE_DATABASE_PASSWORD": _local_secret(instance, "core-role"),
    "POSTGREST_DATABASE_PASSWORD": _local_secret(instance, "postgrest-role"),
  }
  return state, credentials


def _compose(
  state: Mapping[str, Any],
  arguments: tuple[str, ...],
  *,
  timeout: int = 180,
) -> str:
  instance = str(state["identity"])
  return run_database_compose(
    _provider_from_state(state),
    str(state["project"]),
    _runtime_directory(instance) / "compose.env",
    arguments,
    timeout=timeout,
  )


def _published_port(state: Mapping[str, Any], service: str, target: int) -> int:
  output = _compose(state, ("port", service, str(target)), timeout=20).strip()
  match = re.search(r":([0-9]+)$", output)
  if match is None:
    raise RuntimeError(f"could not resolve published port for {service}:{target}")
  return int(match.group(1))


def _fetch_status(url: str, expected: set[int], timeout: float = 3) -> bool:
  try:
    with urlopen(url, timeout=timeout) as response:
      return response.status in expected
  except HTTPError as error:
    return error.code in expected
  except (OSError, URLError):
    return False


def _runtime_is_ready(state: Mapping[str, Any]) -> bool:
  provider = _provider_from_state(state)
  tunnel = state.get("tunnel")
  control_socket_path = tunnel.get("control_socket") if isinstance(tunnel, dict) else None
  if not database_access_ready(provider, control_socket_path):
    return False
  return _fetch_status(f"{state['urls']['core']}readyz", {200}) and _fetch_status(
    str(state["urls"]["postgrest"]),
    {401},
  )


def _wait_for_runtime(state: Mapping[str, Any], timeout: int = 180) -> None:
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if _runtime_is_ready(state):
      return
    time.sleep(0.75)
  raise TimeoutError(f"database runtime {state['identity']} did not become ready")


def _peer_authorization(credentials: Mapping[str, Any]) -> str:
  now = int(time.time())
  token = jwt.encode(
    {
      "role": JWT_ROLE,
      "iss": JWT_ISSUER,
      "aud": JWT_AUDIENCE,
      "iat": now,
      "exp": now + 60,
    },
    str(credentials["JWT_SECRET"]),
    algorithm=JWT_ALGORITHM,
  )
  return f"Bearer {token}"


def _peer_snapshot_ready(peer: Mapping[str, Any], core_url: str) -> bool:
  lease_expires_at = peer.get("lease_expires_at")
  capabilities = peer.get("capabilities")
  if not isinstance(lease_expires_at, str) or not isinstance(capabilities, list):
    return False
  try:
    lease = datetime.datetime.fromisoformat(lease_expires_at.replace("Z", "+00:00"))
  except ValueError:
    return False
  if lease <= datetime.datetime.now(datetime.UTC):
    return False

  base_url = core_url.rstrip("/")
  advertisements = {
    capability.get("id"): capability
    for capability in capabilities
    if isinstance(capability, dict)
  }
  if not set(DEVELOPMENT_CAPABILITIES).issubset(advertisements):
    return False
  for capability, path in DEVELOPMENT_CAPABILITIES.items():
    inbound = advertisements[capability].get("inbound")
    if (
      not isinstance(inbound, dict)
      or inbound.get("protocol") != "core.peer.protocol.http.v1"
    ):
      return False
    parameters = inbound.get("parameters")
    if not isinstance(parameters, dict) or parameters != {
      "method": "POST",
      "url": f"{base_url}{path}",
    }:
      return False
  return True


def _configure_peer_advertisement(
  state: Mapping[str, Any],
  credentials: Mapping[str, Any],
  timeout: int = 40,
) -> None:
  authorization = _peer_authorization(credentials)
  headers = {
    "Authorization": authorization,
    "Content-Profile": "inkcre",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
  }
  peer_url = f"{state['urls']['postgrest']}peers?id=eq.{DEVELOPMENT_PEER_ID}"
  request = Request(
    peer_url,
    method="PATCH",
    headers=headers,
    data=json.dumps(
      {
        "config": {
          "http_public_base_url": str(state["urls"]["core"]).rstrip("/"),
        }
      }
    ).encode(),
  )
  with urlopen(request, timeout=5) as response:
    updated = json.load(response)
  if not isinstance(updated, list) or len(updated) != 1:
    raise RuntimeError("core Peer registration was not available for runtime configuration")

  query_url = (
    f"{state['urls']['postgrest']}peers?select=capabilities,lease_expires_at"
    f"&id=eq.{DEVELOPMENT_PEER_ID}"
  )
  deadline = time.monotonic() + timeout
  query_headers = {"Authorization": authorization}
  while time.monotonic() < deadline:
    with urlopen(Request(query_url, headers=query_headers), timeout=5) as response:
      peers = json.load(response)
    if (
      isinstance(peers, list)
      and len(peers) == 1
      and isinstance(peers[0], dict)
      and _peer_snapshot_ready(peers[0], str(state["urls"]["core"]))
    ):
      return
    time.sleep(0.25)
  raise TimeoutError("core Peer did not publish the expected development capabilities")


def _readiness(state: Mapping[str, Any]) -> dict[str, Any]:
  result = json.loads(
    _compose(
      state,
      (
        "run",
        "--rm",
        "--no-deps",
        "init",
        "python",
        "scripts/container.py",
        "db",
        "ready",
        "--profile",
        "development",
        "--json",
      ),
      timeout=45,
    )
  )
  if not isinstance(result, dict):
    raise RuntimeError("database readiness returned an invalid payload")
  return {
    **result,
    "runtime": {
      "instance": state["identity"],
      "owner_repository": OWNER_REPOSITORY,
      "compose_project": state["project"],
      "docker_daemon_id": state["docker"]["daemon_id"],
      "source_revision": state["source_revision"],
      "source_fingerprint": state["source_fingerprint"],
    },
  }


def ensure(instance: str) -> dict[str, Any]:
  """Create or converge the exact runtime owned by this core-py worktree."""
  instance = _validate_instance(instance)
  configured_provider = resolve_database_provider()
  directory = _runtime_directory(instance)

  if (directory / "runtime.json").is_file():
    state = _read_state(instance)
    if not same_database_provider(
      _provider_from_state(state),
      configured_provider,
    ):
      raise RuntimeError(
        f"database runtime {instance} belongs to another provider; stop it first"
      )
    credentials = _read_json(directory / "credential.json")
    state["docker"] = diagnose_database_provider(configured_provider).as_dict()
    _refresh_artifact_state(state)
  else:
    diagnostics = diagnose_database_provider(configured_provider)
    state, credentials = _new_state(instance, configured_provider, diagnostics)

  state["converging"] = True
  _write_runtime_files(state, credentials)
  previous_remote_ports = state.get("remote_ports")
  _compose(
    state,
    (
      "up",
      "--build",
      "--detach",
      "--remove-orphans",
      "postgres",
      "init",
      "core",
      "postgrest",
    ),
    timeout=600,
  )
  remote_ports = {
    "postgres": _published_port(state, "postgres", 5432),
    "core": _published_port(state, "core", 8000),
    "postgrest": _published_port(state, "postgrest", 3000),
  }
  if state["provider"]["kind"] == "local":
    state["local_ports"] = remote_ports
  elif previous_remote_ports != remote_ports:
    close_database_access(
      configured_provider,
      state["tunnel"].get("control_socket"),
    )
    state["tunnel"]["control_socket"] = None
  state["remote_ports"] = remote_ports
  state["tunnel"]["control_socket"] = open_database_access(
    configured_provider,
    instance,
    state["local_ports"],
    remote_ports,
    state["tunnel"].get("control_socket"),
  )
  _write_runtime_files(state, credentials)
  _wait_for_runtime(state)
  _configure_peer_advertisement(state, credentials)
  readiness = _readiness(state)
  if readiness.get("status") != "ok":
    raise RuntimeError("development database contract readiness failed")
  state["converging"] = False
  _write_runtime_files(state, credentials)
  (directory / "readiness.json").write_text(_stable_json(readiness))
  return {
    "status": "ok",
    "runtime": state,
    "profile": str(directory / "profile.json"),
    "credential": str(directory / "credential.json"),
    "readiness": str(directory / "readiness.json"),
  }


def status(instance: str, *, probe: bool = False) -> tuple[int, dict[str, Any]]:
  """Report instance provenance without starting or taking over a runtime."""
  try:
    state = _read_state(_validate_instance(instance))
    provider_matches = same_database_provider(
      _provider_from_state(state),
      resolve_database_provider(),
    )
    source_matches = state.get("source_fingerprint") == _source_fingerprint()
    ready = provider_matches and _runtime_is_ready(state)
    converged = not state.get("converging", False)
    result = {
      "status": "ok" if ready and source_matches and converged else "error",
      "ready": ready,
      "source_matches": source_matches,
      "provider_matches": provider_matches,
      "converged": converged,
      "runtime": state,
      "profile": str(_runtime_directory(instance) / "profile.json"),
    }
  except (FileNotFoundError, ValueError, RuntimeError) as error:
    result = {
      "status": "error",
      "ready": False,
      "source_matches": False,
      "provider_matches": False,
      "reason": str(error),
      "runtime": {
        "instance": instance,
        "owner_repository": OWNER_REPOSITORY,
      },
    }
  return (0 if result["status"] == "ok" or not probe else 1), result


def reset(instance: str, confirmed: bool) -> dict[str, Any]:
  """Reset only this owner-controlled development database."""
  if not confirmed:
    raise ValueError("reset requires --yes")
  state = _read_state(_validate_instance(instance))
  _compose(
    state,
    (
      "run",
      "--rm",
      "--no-deps",
      "init",
      "python",
      "scripts/container.py",
      "db",
      "reset-dev",
      "--confirm",
      "reset-development-data",
    ),
    timeout=120,
  )
  readiness = _readiness(state)
  (_runtime_directory(instance) / "readiness.json").write_text(_stable_json(readiness))
  return readiness


def stop(instance: str) -> dict[str, Any]:
  """Remove only the exact Compose project, volume, state, and tunnel."""
  instance = _validate_instance(instance)
  state = _read_state(instance)
  provider = _provider_from_state(state)
  try:
    _compose(
      state,
      ("down", "--volumes", "--remove-orphans"),
      timeout=180,
    )
  finally:
    close_database_access(provider, state["tunnel"].get("control_socket"))
  shutil.rmtree(_runtime_directory(instance))
  return {
    "status": "ok",
    "removed": {
      "instance": instance,
      "owner_repository": OWNER_REPOSITORY,
      "compose_project": state["project"],
      "docker_daemon_id": state["docker"]["daemon_id"],
    },
  }


def _svc_instance() -> str:
  result = subprocess.run(  # noqa: S603
    ("svc", "dev", "identity", "--repo", str(PROJECT_ROOT), "--json"),
    cwd=PROJECT_ROOT,
    check=True,
    capture_output=True,
    text=True,
  )
  identity = json.loads(result.stdout)
  return _validate_instance(identity["workspace"]["instance"])


def _parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "command",
    choices=("ensure", "probe", "ready", "reset", "status", "stop"),
  )
  parser.add_argument("instance", nargs="?")
  parser.add_argument("--yes", action="store_true")
  return parser


def main(argv: list[str] | None = None) -> int:
  """Execute the development runtime owner command surface."""
  arguments = _parser().parse_args(argv)
  instance = arguments.instance or _svc_instance()
  try:
    if arguments.command == "ensure":
      result = ensure(instance)
      exit_code = 0
    elif arguments.command == "probe":
      exit_code, result = status(instance, probe=True)
    elif arguments.command == "ready":
      result = _readiness(_read_state(instance))
      exit_code = 0 if result.get("status") == "ok" else 1
    elif arguments.command == "reset":
      result = reset(instance, arguments.yes)
      exit_code = 0 if result.get("status") == "ok" else 1
    elif arguments.command == "status":
      exit_code, result = status(instance)
    else:
      result = stop(instance)
      exit_code = 0
  except (
    FileNotFoundError,
    OSError,
    RuntimeError,
    subprocess.SubprocessError,
    TimeoutError,
    ValueError,
  ) as error:
    print(f"ERROR: {error}", file=sys.stderr)
    return 1
  print(_stable_json(result), end="")
  return exit_code


if __name__ == "__main__":
  raise SystemExit(main())
