"""Local and SSH Docker transports for the development database runtime."""

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
import json
import os
import shlex
import socket
import subprocess
import tarfile
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
REMOTE_RUNNER = PROJECT_ROOT / "scripts" / "remote-compose.sh"
BASE_CONFIG = PROJECT_ROOT / "svc.json"
LOCAL_CONFIG = PROJECT_ROOT / "svc.local.json"
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"

BUILD_CONTEXT_PATHS = (
  Path("Dockerfile"),
  Path("README.md"),
  Path("LICENSE"),
  Path("alembic.ini"),
  Path("app"),
  Path("inkcre_core"),
  Path("extensions"),
  Path("libs"),
  Path("migrations"),
  Path("pdm.lock"),
  Path("pyproject.toml"),
  Path("run.py"),
  Path("scripts"),
  Path("utils"),
)
EXCLUDED_BUILD_PARTS = {
  ".git",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
  ".venv",
  "__pycache__",
}
SSH_ALIAS_CHARACTERS = frozenset(
  "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


@dataclass(frozen=True)
class DatabaseProvider:
  """Validated Docker provider selected by committed plus machine-local SVC config."""

  kind: str
  target: str | None = None
  docker_bin: str | None = None
  forward_host: str | None = None

  def as_dict(self) -> dict[str, str | None]:
    """Return a stable JSON representation."""
    return asdict(self)


@dataclass(frozen=True)
class ProviderDiagnostics:
  """Stable identity of the selected Docker daemon."""

  kind: str
  engine: str
  compose: str
  daemon_id: str
  daemon_name: str
  target: str | None = None

  def as_dict(self) -> dict[str, str | None]:
    """Return a stable JSON representation."""
    return asdict(self)


def _optional_json(path: Path) -> dict:
  if not path.is_file():
    return {}
  value = json.loads(path.read_text())
  if not isinstance(value, dict):
    raise ValueError(f"{path.name} must contain a JSON object")
  return value


def _provision_environment(
  config: dict,
  legacy_profile: str | None,
) -> dict[str, str]:
  dev = config.get("dev", {})
  targets = dev.get("targets")
  if targets is None:
    profile = dev.get("profile") or legacy_profile
    targets = dev.get("profiles", {}).get(profile, {}).get("targets", {})
  try:
    environment = targets["database"]["provision"].get("env", {})
  except (KeyError, TypeError):
    return {}
  if not isinstance(environment, dict) or not all(
    isinstance(name, str) and isinstance(value, str) for name, value in environment.items()
  ):
    raise ValueError("database provision environment must contain string values")
  return environment


def _declared_provider_environment() -> dict[str, str]:
  base = _optional_json(BASE_CONFIG)
  local = _optional_json(LOCAL_CONFIG)
  legacy_profile = local.get("dev", {}).get("profile") or base.get("dev", {}).get("profile")
  return {
    **_provision_environment(base, legacy_profile),
    **_provision_environment(local, legacy_profile),
  }


def _configured_value(name: str, declared: dict[str, str]) -> str | None:
  return os.environ.get(name) or declared.get(name)


def _validate_ssh_alias(value: str, label: str) -> str:
  if (
    not value
    or len(value) > 128
    or not value[0].isalnum()
    or any(character not in SSH_ALIAS_CHARACTERS for character in value)
  ):
    raise ValueError(f"{label} must be one SSH-config host alias")
  return value


def _validate_remote_value(value: str, label: str) -> str:
  if not value or len(value) > 512 or "\0" in value or "\n" in value or "\r" in value:
    raise ValueError(f"{label} must be one non-empty line")
  return value


def quote_environment_value(value: str) -> str:
  """Quote one already newline-validated value for a POSIX env file."""
  _validate_remote_value(value, "environment value")
  return "'" + value.replace("'", "'\\''") + "'"


def resolve_database_provider() -> DatabaseProvider:
  """Resolve the portable local default or one machine-local SSH provider."""
  declared = _declared_provider_environment()
  kind = _configured_value("INKCRE_DATABASE_PROVIDER", declared) or "local"
  if kind == "local":
    return DatabaseProvider(kind="local")
  if kind != "ssh":
    raise ValueError(f"unsupported database provider: {kind}")

  target = _configured_value("INKCRE_DATABASE_SSH_TARGET", declared)
  if target is None:
    raise ValueError("SSH database provider requires INKCRE_DATABASE_SSH_TARGET")
  docker_bin = _configured_value("INKCRE_DATABASE_SSH_DOCKER_BIN", declared) or "docker"
  forward_host = (
    _configured_value("INKCRE_DATABASE_SSH_FORWARD_HOST", declared) or "127.0.0.1"
  )
  return DatabaseProvider(
    kind=kind,
    target=_validate_ssh_alias(target, "SSH database provider target"),
    docker_bin=_validate_remote_value(docker_bin, "remote Docker executable"),
    forward_host=_validate_ssh_alias(forward_host, "remote forwarding host"),
  )


def same_database_provider(left: DatabaseProvider, right: DatabaseProvider) -> bool:
  """Compare provider authority without relying on display strings."""
  return left == right


def available_port() -> int:
  """Reserve and release one collision-safe local loopback port."""
  with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    return int(listener.getsockname()[1])


def _run(
  arguments: Sequence[str],
  *,
  cwd: Path = PROJECT_ROOT,
  input_bytes: bytes | None = None,
  timeout: int = 180,
) -> subprocess.CompletedProcess:
  return subprocess.run(  # noqa: S603
    list(arguments),
    cwd=cwd,
    input=input_bytes,
    check=True,
    capture_output=True,
    timeout=timeout,
  )


def _compose_failure(
  error: subprocess.CalledProcessError,
  compose_environment: Path,
  provider: DatabaseProvider,
) -> RuntimeError:
  raw_diagnostics = b"\n".join(value for value in (error.stderr, error.stdout) if value)
  diagnostics = raw_diagnostics.decode(errors="replace")
  try:
    for line in compose_environment.read_text().splitlines():
      tokens = shlex.split(line, comments=False, posix=True)
      if len(tokens) != 1 or "=" not in tokens[0]:
        continue
      name, value = tokens[0].split("=", 1)
      if value and any(marker in name for marker in ("PASSWORD", "SECRET", "TOKEN")):
        diagnostics = diagnostics.replace(value, "[REDACTED]")
  except (OSError, ValueError):
    diagnostics = "provider diagnostics could not be decoded safely"

  diagnostics = "".join(
    character for character in diagnostics if character in "\n\r\t" or ord(character) >= 32
  ).strip()
  if len(diagnostics) > 4096:
    diagnostics = f"…{diagnostics[-4095:]}"
  if not diagnostics:
    diagnostics = "the provider returned no diagnostics"
  return RuntimeError(
    f"{provider.kind} Docker Compose failed with exit {error.returncode}: {diagnostics}"
  )


def _build_context_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
  parts = Path(info.name).parts
  if any(part in EXCLUDED_BUILD_PARTS for part in parts):
    return None
  if info.name.endswith((".pyc", ".pyo")) or info.name.endswith("/.pdm-python"):
    return None
  return info


def iter_build_context_files() -> Iterable[Path]:
  """Yield the exact source surface admitted to the OCI development build."""
  for relative_path in BUILD_CONTEXT_PATHS:
    path = PROJECT_ROOT / relative_path
    if not path.exists():
      raise FileNotFoundError(f"required Docker build input is missing: {relative_path}")
    if path.is_file():
      yield path
      continue
    for child in sorted(path.rglob("*")):
      if child.is_file() and not any(
        part in EXCLUDED_BUILD_PARTS for part in child.relative_to(PROJECT_ROOT).parts
      ):
        if not child.name.endswith((".pyc", ".pyo")) and child.name != ".pdm-python":
          yield child


def _remote_payload(
  compose_environment: Path,
  arguments: Sequence[str],
) -> bytes:
  for argument in arguments:
    if "\0" in argument or "\n" in argument or "\r" in argument:
      raise ValueError("Compose arguments must be newline-free")

  payload = BytesIO()
  with tarfile.open(fileobj=payload, mode="w", format=tarfile.USTAR_FORMAT) as archive:
    archive.add(COMPOSE_FILE, arcname="database.compose.yml")
    archive.add(compose_environment, arcname="compose.env")
    archive.add(REMOTE_RUNNER, arcname="remote-compose.sh")

    encoded_arguments = f"{'\n'.join(arguments)}\n".encode()
    argument_info = tarfile.TarInfo("compose.args")
    argument_info.size = len(encoded_arguments)
    argument_info.mode = 0o600
    archive.addfile(argument_info, BytesIO(encoded_arguments))

    for path in BUILD_CONTEXT_PATHS:
      archive.add(
        PROJECT_ROOT / path,
        arcname=str(Path("context") / path),
        filter=_build_context_filter,
      )
  return payload.getvalue()


REMOTE_BOOTSTRAP = (
  "set -eu; "
  'payload_dir=$(mktemp -d "${TMPDIR:-/tmp}/inkcre-compose.XXXXXX"); '
  "trap 'rm -rf \"$payload_dir\"' EXIT HUP INT TERM; "
  'tar -m -xf - -C "$payload_dir"; '
  'chmod 700 "$payload_dir/remote-compose.sh"; '
  '"$payload_dir/remote-compose.sh" "$payload_dir"'
)


def run_database_compose(
  provider: DatabaseProvider,
  project: str,
  compose_environment: Path,
  arguments: Sequence[str],
  *,
  timeout: int = 180,
) -> str:
  """Run one bounded Compose operation through the selected provider."""
  try:
    if provider.kind == "local":
      result = _run(
        (
          "docker",
          "compose",
          "--file",
          str(COMPOSE_FILE),
          "--env-file",
          str(compose_environment),
          "--project-name",
          project,
          *arguments,
        ),
        timeout=timeout,
      )
    else:
      if provider.target is None:
        raise ValueError("SSH database provider target is missing")
      result = _run(
        (
          "ssh",
          "-T",
          "-o",
          "BatchMode=yes",
          provider.target,
          REMOTE_BOOTSTRAP,
        ),
        input_bytes=_remote_payload(compose_environment, arguments),
        timeout=timeout,
      )
  except subprocess.CalledProcessError as error:
    raise _compose_failure(error, compose_environment, provider) from error
  return result.stdout.decode()


def _temporary_provider_environment(provider: DatabaseProvider) -> Path:
  directory = Path(tempfile.mkdtemp(prefix="inkcre-provider-"))
  path = directory / "compose.env"
  docker_bin = provider.docker_bin or "docker"
  path.write_text(
    "\n".join(
      (
        "INKCRE_COMPOSE_PROJECT_NAME='inkcre-provider-check'",
        f"INKCRE_REMOTE_DOCKER_BIN={quote_environment_value(docker_bin)}",
        "",
      )
    )
  )
  path.chmod(0o600)
  return path


def diagnose_database_provider(provider: DatabaseProvider) -> ProviderDiagnostics:
  """Resolve the exact Docker daemon and Compose implementation."""
  if provider.kind == "local":
    identity = _run(
      (
        "docker",
        "info",
        "--format",
        "{{.ID}}\n{{.Name}}\n{{.ServerVersion}}",
      ),
      timeout=15,
    ).stdout.decode()
    compose = _run(("docker", "compose", "version", "--short"), timeout=15).stdout.decode()
  else:
    if provider.target is None:
      raise ValueError("SSH database provider target is missing")
    _run(("ssh", "-G", provider.target), timeout=5)
    temporary_environment = _temporary_provider_environment(provider)
    try:
      output = run_database_compose(
        provider,
        "inkcre-provider-check",
        temporary_environment,
        ("__provider-check__",),
        timeout=20,
      )
    finally:
      for child in temporary_environment.parent.iterdir():
        child.unlink()
      temporary_environment.parent.rmdir()
    *identity_lines, compose = output.strip().splitlines()
    identity = "\n".join(identity_lines)

  daemon_id, daemon_name, engine = identity.strip().splitlines()
  if not all((daemon_id, daemon_name, engine, compose.strip())):
    raise RuntimeError("Docker provider returned incomplete diagnostics")
  return ProviderDiagnostics(
    kind=provider.kind,
    target=provider.target,
    daemon_id=daemon_id,
    daemon_name=daemon_name,
    engine=engine,
    compose=compose.strip(),
  )


def control_socket(identity: str) -> Path:
  """Return the instance-owned OpenSSH control socket path."""
  return RUNTIME_ROOT / "ssh" / identity


def database_access_ready(
  provider: DatabaseProvider,
  control_socket_path: str | None,
) -> bool:
  """Check whether an SSH runtime still owns its forwarding process."""
  if provider.kind == "local":
    return True
  if provider.target is None or control_socket_path is None:
    return False
  try:
    _run(
      (
        "ssh",
        "-S",
        Path(control_socket_path).name,
        "-O",
        "check",
        provider.target,
      ),
      cwd=Path(control_socket_path).parent,
      timeout=5,
    )
  except (OSError, subprocess.SubprocessError):
    return False
  return True


def open_database_access(
  provider: DatabaseProvider,
  identity: str,
  local_ports: dict[str, int],
  remote_ports: dict[str, int],
  current_socket: str | None,
) -> str | None:
  """Open one instance-owned tunnel and return its control socket."""
  if provider.kind == "local":
    return None
  if provider.target is None or provider.forward_host is None:
    raise ValueError("SSH database provider is incomplete")
  if database_access_ready(provider, current_socket):
    return current_socket

  socket_path = control_socket(identity)
  socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
  socket_path.unlink(missing_ok=True)
  forwards: list[str] = []
  for name in sorted(local_ports):
    forwards.extend(
      (
        "-L",
        f"{local_ports[name]}:{provider.forward_host}:{remote_ports[name]}",
      )
    )
  _run(
    (
      "ssh",
      "-M",
      "-S",
      socket_path.name,
      "-fnNT",
      "-o",
      "BatchMode=yes",
      "-o",
      "ExitOnForwardFailure=yes",
      *forwards,
      provider.target,
    ),
    # OpenSSH appends a temporary suffix; absolute worktree paths can exceed AF_UNIX limits.
    cwd=socket_path.parent,
    timeout=15,
  )
  return str(socket_path)


def close_database_access(
  provider: DatabaseProvider,
  control_socket_path: str | None,
) -> None:
  """Close only the forwarding process named by the runtime descriptor."""
  if provider.kind == "local" or provider.target is None or control_socket_path is None:
    return
  try:
    _run(
      (
        "ssh",
        "-S",
        Path(control_socket_path).name,
        "-O",
        "exit",
        provider.target,
      ),
      cwd=Path(control_socket_path).parent,
      timeout=5,
    )
  except (OSError, subprocess.SubprocessError):
    pass
  Path(control_socket_path).unlink(missing_ok=True)
