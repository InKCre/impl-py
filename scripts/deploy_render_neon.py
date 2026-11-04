"""Converge one self-hosted Render + Neon deployment from an exact Git commit."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
import typing
from urllib.parse import unquote, urlsplit
import uuid

import httpx


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from inkcre_core.database_contract.constants import JWT_MINIMUM_SECRET_BYTES
from app.database_contract.lifecycle import initialize
from app.database_contract.readiness import check_database_contract
from app.database_contract.roles import RoleSecrets
from scripts.configure_peer_runtime import configure_peer_runtime
from scripts.rebind_database_url import rebind_database_url
from scripts.verify_postgrest_contract import verify as verify_postgrest


RENDER_API_BASE_URL = "https://api.render.com/v1"
DEPLOY_FAILURE_STATUSES = frozenset(
  {
    "build_failed",
    "update_failed",
    "pre_deploy_failed",
    "canceled",
    "deactivated",
  }
)
DEPLOY_SUCCESS_STATUS = "live"
SERVICE_PREFIX_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DEPLOYMENT_PROFILE_ID = "core-py.render-neon.v1"


class RenderAPIError(RuntimeError):
  """A non-secret, actionable Render control-plane failure."""


class DeploymentInputError(ValueError):
  """A non-secret deployment input failure safe for workflow output."""


@dataclass(frozen=True)
class RenderService:
  id: str
  name: str
  url: str
  runtime: str
  plan: str
  region: str

  @classmethod
  def from_document(cls, document: dict[str, typing.Any]) -> "RenderService":
    details = document.get("serviceDetails")
    if not isinstance(details, dict):
      raise RenderAPIError("Render service response omitted serviceDetails")
    values = {
      "id": document.get("id"),
      "name": document.get("name"),
      "url": details.get("url"),
      "runtime": details.get("runtime"),
      "plan": details.get("plan"),
      "region": details.get("region"),
    }
    if not all(isinstance(value, str) and value for value in values.values()):
      raise RenderAPIError("Render service response omitted an identity field")
    return cls(**typing.cast(dict[str, str], values))


@dataclass(frozen=True)
class RenderServiceSpec:
  name: str
  repository: str
  branch: str
  dockerfile: str
  environment: dict[str, str]
  health_check_path: str | None = None
  region: str = "singapore"

  def creation_document(self, owner_id: str) -> dict[str, typing.Any]:
    service_details: dict[str, typing.Any] = {
      "runtime": "docker",
      "plan": "free",
      "region": self.region,
      "envSpecificDetails": {
        "dockerContext": ".",
        "dockerfilePath": self.dockerfile,
      },
    }
    if self.health_check_path is not None:
      service_details["healthCheckPath"] = self.health_check_path
    return {
      "type": "web_service",
      "name": self.name,
      "ownerId": owner_id,
      "repo": self.repository,
      "branch": self.branch,
      "autoDeploy": "no",
      "envVars": [
        {"key": key, "value": value} for key, value in sorted(self.environment.items())
      ],
      "serviceDetails": service_details,
    }

  def update_document(self) -> dict[str, typing.Any]:
    service_details: dict[str, typing.Any] = {
      "runtime": "docker",
      "plan": "free",
      "envSpecificDetails": {
        "dockerContext": ".",
        "dockerfilePath": self.dockerfile,
      },
    }
    if self.health_check_path is not None:
      service_details["healthCheckPath"] = self.health_check_path
    return {
      "repo": self.repository,
      "branch": self.branch,
      "autoDeploy": "no",
      "serviceDetails": service_details,
    }


@dataclass(frozen=True)
class DeploymentResult:
  commit: str
  core_service: str
  core_url: str
  postgrest_service: str
  postgrest_url: str
  peer_id: uuid.UUID

  def as_dict(self) -> dict[str, str]:
    return {
      "commit": self.commit,
      "core_service": self.core_service,
      "core_url": self.core_url,
      "postgrest_service": self.postgrest_service,
      "postgrest_url": self.postgrest_url,
      "peer_id": str(self.peer_id),
    }


@dataclass(frozen=True)
class DeploymentRequest:
  owner_id: str
  repository: str
  branch: str
  commit: str
  service_prefix: str
  migration_database_url: str
  source_database_url: str
  jwt_secret: str
  wait_seconds: float


class RenderClient:
  """Small Render API boundary; it never logs response bodies or env values."""

  def __init__(
    self,
    api_key: str,
    *,
    base_url: str = RENDER_API_BASE_URL,
    transport: httpx.BaseTransport | None = None,
  ) -> None:
    if not api_key:
      raise ValueError("Render API key is required")
    self._client = httpx.Client(
      base_url=base_url,
      headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
      },
      timeout=30,
      transport=transport,
    )

  def close(self) -> None:
    self._client.close()

  def __enter__(self) -> "RenderClient":
    return self

  def __exit__(self, *_args: object) -> None:
    self.close()

  def _request(
    self,
    method: str,
    path: str,
    *,
    expected: tuple[int, ...] = (200,),
    **kwargs: typing.Any,
  ) -> httpx.Response:
    try:
      response = self._client.request(method, path, **kwargs)
    except httpx.HTTPError as error:
      raise RenderAPIError(f"Render {method} {path} did not complete") from error
    if response.status_code not in expected:
      raise RenderAPIError(f"Render {method} {path} returned HTTP {response.status_code}")
    return response

  @staticmethod
  def _json(response: httpx.Response) -> typing.Any:
    try:
      return response.json()
    except json.JSONDecodeError as error:
      raise RenderAPIError("Render returned a non-JSON control-plane response") from error

  def find_service(self, owner_id: str, name: str) -> RenderService | None:
    response = self._request(
      "GET",
      "/services",
      params={"ownerId": owner_id, "name": name, "limit": 100},
    )
    document = self._json(response)
    if not isinstance(document, list):
      raise RenderAPIError("Render service list response was not a list")
    matches = []
    for item in document:
      if not isinstance(item, dict) or not isinstance(item.get("service"), dict):
        raise RenderAPIError("Render service list item was malformed")
      service_document = typing.cast(dict[str, typing.Any], item["service"])
      if service_document.get("name") == name:
        matches.append(RenderService.from_document(service_document))
    if len(matches) > 1:
      raise RenderAPIError(f"Render returned duplicate service name {name!r}")
    return matches[0] if matches else None

  def get_environment(self, service_id: str) -> dict[str, str]:
    environment: dict[str, str] = {}
    cursor: str | None = None
    while True:
      params: dict[str, str | int] = {"limit": 100}
      if cursor is not None:
        params["cursor"] = cursor
      response = self._request(
        "GET",
        f"/services/{service_id}/env-vars",
        params=params,
      )
      document = self._json(response)
      if not isinstance(document, list):
        raise RenderAPIError("Render environment response was not a list")
      for item in document:
        if not isinstance(item, dict) or not isinstance(item.get("envVar"), dict):
          raise RenderAPIError("Render environment item was malformed")
        env_var = typing.cast(dict[str, typing.Any], item["envVar"])
        key = env_var.get("key")
        value = env_var.get("value")
        if not isinstance(key, str) or not isinstance(value, str):
          raise RenderAPIError("Render environment item omitted key or value")
        environment[key] = value
      if len(document) < 100:
        return environment
      next_cursor = document[-1].get("cursor")
      if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
        raise RenderAPIError("Render environment pagination cursor was malformed")
      cursor = next_cursor

  def merge_environment(self, service_id: str, environment: dict[str, str]) -> None:
    merged = self.get_environment(service_id)
    merged.update(environment)
    self._request(
      "PUT",
      f"/services/{service_id}/env-vars",
      json=[{"key": key, "value": value} for key, value in sorted(merged.items())],
    )

  def create_service(
    self,
    owner_id: str,
    spec: RenderServiceSpec,
  ) -> tuple[RenderService, str | None]:
    response = self._request(
      "POST",
      "/services",
      expected=(201,),
      json=spec.creation_document(owner_id),
    )
    document = self._json(response)
    if not isinstance(document, dict) or not isinstance(document.get("service"), dict):
      raise RenderAPIError("Render create-service response was malformed")
    deploy_id = document.get("deployId")
    if deploy_id is not None and not isinstance(deploy_id, str):
      raise RenderAPIError("Render create-service deploy identity was malformed")
    return (
      RenderService.from_document(typing.cast(dict[str, typing.Any], document["service"])),
      deploy_id,
    )

  def update_service(
    self,
    service: RenderService,
    spec: RenderServiceSpec,
  ) -> RenderService:
    if service.runtime != "docker":
      raise RenderAPIError(f"Existing service {service.name!r} is not Docker-backed")
    if service.region != spec.region:
      raise RenderAPIError(
        f"Existing service {service.name!r} is in region {service.region!r}; "
        f"expected {spec.region!r}"
      )
    response = self._request(
      "PATCH",
      f"/services/{service.id}",
      json=spec.update_document(),
    )
    document = self._json(response)
    if not isinstance(document, dict):
      raise RenderAPIError("Render update-service response was malformed")
    updated = RenderService.from_document(document)
    self.merge_environment(updated.id, spec.environment)
    return updated

  def converge_service(
    self,
    owner_id: str,
    existing: RenderService | None,
    spec: RenderServiceSpec,
  ) -> tuple[RenderService, str | None]:
    if existing is None:
      return self.create_service(owner_id, spec)
    return self.update_service(existing, spec), None

  def trigger_deploy(self, service_id: str, commit: str) -> str:
    response = self._request(
      "POST",
      f"/services/{service_id}/deploys",
      expected=(201,),
      json={"commitId": commit, "clearCache": "do_not_clear"},
    )
    document = self._json(response)
    if not isinstance(document, dict) or not isinstance(document.get("id"), str):
      raise RenderAPIError("Render trigger-deploy response omitted the deploy identity")
    return document["id"]

  def get_deploy(self, service_id: str, deploy_id: str) -> dict[str, typing.Any]:
    response = self._request(
      "GET",
      f"/services/{service_id}/deploys/{deploy_id}",
    )
    document = self._json(response)
    if not isinstance(document, dict):
      raise RenderAPIError("Render deploy response was malformed")
    return document

  def wait_for_deploy(
    self,
    service: RenderService,
    deploy_id: str,
    *,
    wait_seconds: float,
  ) -> dict[str, typing.Any]:
    deadline = time.monotonic() + wait_seconds
    while True:
      document = self.get_deploy(service.id, deploy_id)
      status = document.get("status")
      if status == DEPLOY_SUCCESS_STATUS:
        return document
      if status in DEPLOY_FAILURE_STATUSES:
        raise RenderAPIError(
          f"Render deploy for {service.name!r} ended with status {status!r}"
        )
      if time.monotonic() >= deadline:
        raise RenderAPIError(f"Render deploy for {service.name!r} timed out")
      time.sleep(5)


def _database_password(url: str, expected_role: str) -> str:
  parsed = urlsplit(url)
  if unquote(parsed.username or "") != expected_role or parsed.password is None:
    raise RenderAPIError(
      f"Existing Render database URL does not belong to role {expected_role!r}"
    )
  password = unquote(parsed.password)
  if len(password.encode()) < 32:
    raise RenderAPIError(f"Existing password for role {expected_role!r} is too short")
  return password


def _database_coordinate(url: str) -> tuple[str, int | None, str, str]:
  parsed = urlsplit(url)
  if not parsed.hostname or not parsed.path:
    raise RenderAPIError("Database URL omitted its host or database name")
  return parsed.hostname, parsed.port, parsed.path, parsed.query


def _role_password(
  client: RenderClient,
  service: RenderService | None,
  *,
  environment_key: str,
  expected_role: str,
  expected_source_url: str,
) -> str:
  if service is None:
    return secrets.token_urlsafe(32)
  environment = client.get_environment(service.id)
  if environment.get("INKCRE_DEPLOYMENT_PROFILE") != DEPLOYMENT_PROFILE_ID:
    raise RenderAPIError(
      f"Existing service {service.name!r} is not owned by this deployment profile"
    )
  database_url = environment.get(environment_key)
  if not database_url:
    raise RenderAPIError(
      f"Existing service {service.name!r} has no {environment_key}; "
      "refusing silent credential rotation"
    )
  if _database_coordinate(database_url) != _database_coordinate(expected_source_url):
    raise RenderAPIError(
      f"Existing service {service.name!r} addresses another database; "
      "refusing silent database rebind"
    )
  return _database_password(database_url, expected_role)


def _deploy_exact_commit(
  client: RenderClient,
  service: RenderService,
  initial_deploy_id: str | None,
  commit: str,
  *,
  wait_seconds: float,
) -> None:
  deploy_id = initial_deploy_id or client.trigger_deploy(service.id, commit)
  document = client.wait_for_deploy(
    service,
    deploy_id,
    wait_seconds=wait_seconds,
  )
  deployed_commit = document.get("commit")
  deployed_commit_id = (
    deployed_commit.get("id") if isinstance(deployed_commit, dict) else None
  )
  if deployed_commit_id == commit:
    return
  if initial_deploy_id is None:
    raise RenderAPIError(
      f"Render deploy for {service.name!r} did not report the requested commit"
    )
  exact_deploy = client.trigger_deploy(service.id, commit)
  document = client.wait_for_deploy(
    service,
    exact_deploy,
    wait_seconds=wait_seconds,
  )
  deployed_commit = document.get("commit")
  if not isinstance(deployed_commit, dict) or deployed_commit.get("id") != commit:
    raise RenderAPIError(
      f"Render deploy for {service.name!r} did not converge to the exact commit"
    )


def _probe_core(url: str, *, wait_seconds: float) -> None:
  deadline = time.monotonic() + wait_seconds
  while True:
    try:
      response = httpx.get(f"{url.rstrip('/')}/readyz", timeout=15)
      if response.status_code == 200:
        return
    except httpx.HTTPError:
      pass
    if time.monotonic() >= deadline:
      raise RuntimeError("Render core readiness probe timed out")
    time.sleep(5)


def _probe_postgrest(url: str, *, wait_seconds: float) -> None:
  """Wait until Render routes requests to the configured PostgREST runtime."""
  deadline = time.monotonic() + wait_seconds
  endpoint = f"{url.rstrip('/')}/peers?select=id&limit=1"
  while True:
    try:
      response = httpx.get(endpoint, timeout=15)
      if response.status_code == 401:
        return
    except httpx.HTTPError:
      pass
    if time.monotonic() >= deadline:
      raise RuntimeError("Render PostgREST availability probe timed out")
    time.sleep(5)


def _validate_inputs(
  *,
  service_prefix: str,
  commit: str,
  jwt_secret: str,
  wait_seconds: float,
) -> None:
  if not SERVICE_PREFIX_PATTERN.fullmatch(service_prefix):
    raise DeploymentInputError(
      "service prefix must be 3-40 lowercase letters, digits, or internal hyphens"
    )
  if not COMMIT_PATTERN.fullmatch(commit):
    raise DeploymentInputError("commit must be one lowercase 40-character Git SHA")
  if len(jwt_secret.encode()) < JWT_MINIMUM_SECRET_BYTES:
    raise DeploymentInputError(
      f"JWT_SECRET must be at least {JWT_MINIMUM_SECRET_BYTES} bytes"
    )
  if wait_seconds <= 0:
    raise DeploymentInputError("wait-seconds must be positive")


def deploy_render_neon(
  client: RenderClient,
  request: DeploymentRequest,
) -> DeploymentResult:
  """Converge database, services, exact source, discovery, and probes."""
  _validate_inputs(
    service_prefix=request.service_prefix,
    commit=request.commit,
    jwt_secret=request.jwt_secret,
    wait_seconds=request.wait_seconds,
  )
  core_name = f"{request.service_prefix}-core"
  postgrest_name = f"{request.service_prefix}-postgrest"
  core_existing = client.find_service(request.owner_id, core_name)
  postgrest_existing = client.find_service(request.owner_id, postgrest_name)

  core_password = _role_password(
    client,
    core_existing,
    environment_key="DATABASE_URL",
    expected_role="inkcre_core",
    expected_source_url=request.source_database_url,
  )
  postgrest_password = _role_password(
    client,
    postgrest_existing,
    environment_key="PGRST_DB_URI",
    expected_role="authenticator",
    expected_source_url=request.source_database_url,
  )

  previous_migration_url = os.environ.get("MIGRATION_DATABASE_URL")
  os.environ["MIGRATION_DATABASE_URL"] = request.migration_database_url
  try:
    initialize(
      "runtime",
      environment="runtime",
      secrets=RoleSecrets(
        authenticator_password=postgrest_password,
        core_runtime_password=core_password,
      ),
    )
    readiness = check_database_contract("runtime")
  finally:
    if previous_migration_url is None:
      os.environ.pop("MIGRATION_DATABASE_URL", None)
    else:
      os.environ["MIGRATION_DATABASE_URL"] = previous_migration_url
  if not readiness.ready:
    raise RuntimeError(f"database did not converge: {readiness.reason}")

  core_database_url = rebind_database_url(
    request.source_database_url,
    role="inkcre_core",
    password=core_password,
    scheme="postgresql+psycopg",
  )
  postgrest_database_url = rebind_database_url(
    request.source_database_url,
    role="authenticator",
    password=postgrest_password,
    scheme="postgresql",
  )
  peer_id = uuid.uuid5(
    uuid.NAMESPACE_URL,
    f"render:{request.owner_id}:{core_name}",
  )

  core_spec = RenderServiceSpec(
    name=core_name,
    repository=request.repository,
    branch=request.branch,
    dockerfile="./Dockerfile",
    health_check_path="/readyz",
    environment={
      "DATABASE_SCALE_0": "true",
      "DATABASE_URL": core_database_url,
      "INKCRE_DEPLOYMENT_PROFILE": DEPLOYMENT_PROFILE_ID,
      "INKCRE_ENV_FILE": "",
      "JWT_SECRET": request.jwt_secret,
      "OBSRV__LOGGING_BACKEND": "none",
      "PEER_ID": str(peer_id),
      "PEER_NAME": core_name,
      "SKIP_EXTENSION_START": "0",
      "SOURCE_REVISION": request.commit,
    },
  )
  postgrest_spec = RenderServiceSpec(
    name=postgrest_name,
    repository=request.repository,
    branch=request.branch,
    dockerfile="./Dockerfile.postgrest",
    environment={
      "INKCRE_DEPLOYMENT_PROFILE": DEPLOYMENT_PROFILE_ID,
      "PGRST_DB_ANON_ROLE": "anonymous",
      "PGRST_DB_POOL": "2",
      "PGRST_DB_PRE_REQUEST": "inkcre_internal.check_jwt",
      "PGRST_DB_SCHEMAS": "inkcre",
      "PGRST_DB_URI": postgrest_database_url,
      "PGRST_JWT_AUD": "inkcre-api",
      "PGRST_JWT_SECRET": request.jwt_secret,
      "SOURCE_REVISION": request.commit,
    },
  )
  core, core_initial_deploy = client.converge_service(
    request.owner_id,
    core_existing,
    core_spec,
  )
  postgrest, postgrest_initial_deploy = client.converge_service(
    request.owner_id,
    postgrest_existing,
    postgrest_spec,
  )

  _deploy_exact_commit(
    client,
    core,
    core_initial_deploy,
    request.commit,
    wait_seconds=request.wait_seconds,
  )
  _deploy_exact_commit(
    client,
    postgrest,
    postgrest_initial_deploy,
    request.commit,
    wait_seconds=request.wait_seconds,
  )
  _probe_core(core.url, wait_seconds=request.wait_seconds)
  _probe_postgrest(postgrest.url, wait_seconds=request.wait_seconds)
  configure_peer_runtime(
    core_database_url,
    peer_id,
    core.url,
    wait_seconds=min(request.wait_seconds, 120),
  )
  verify_postgrest(
    postgrest.url,
    request.jwt_secret,
    secrets.token_urlsafe(32),
  )
  return DeploymentResult(
    commit=request.commit,
    core_service=core.name,
    core_url=core.url,
    postgrest_service=postgrest.name,
    postgrest_url=postgrest.url,
    peer_id=peer_id,
  )


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser()
  parser.add_argument("--repository", required=True)
  parser.add_argument("--branch", required=True)
  parser.add_argument("--commit", required=True)
  parser.add_argument("--service-prefix", required=True)
  parser.add_argument("--wait-seconds", type=float, default=1800)
  parser.add_argument("--result-file", type=Path)
  return parser


def _required_environment(name: str) -> str:
  value = os.getenv(name, "")
  if not value:
    raise DeploymentInputError(f"{name} is required")
  return value


def main() -> int:
  args = build_parser().parse_args()
  try:
    with RenderClient(_required_environment("RENDER_API_KEY")) as client:
      result = deploy_render_neon(
        client,
        DeploymentRequest(
          owner_id=_required_environment("RENDER_OWNER_ID"),
          repository=args.repository,
          branch=args.branch,
          commit=args.commit,
          service_prefix=args.service_prefix,
          migration_database_url=_required_environment("MIGRATION_DATABASE_URL"),
          source_database_url=_required_environment("SOURCE_DATABASE_URL"),
          jwt_secret=_required_environment("JWT_SECRET"),
          wait_seconds=args.wait_seconds,
        ),
      )
  except Exception as error:
    detail = (
      str(error)
      if isinstance(error, (DeploymentInputError, RenderAPIError))
      else "unexpected deployment operation failed"
    )
    print(
      json.dumps(
        {
          "status": "error",
          "reason": "render_neon_deployment_failed",
          "error_type": type(error).__name__,
          "detail": detail,
        },
        sort_keys=True,
      ),
      file=sys.stderr,
    )
    return 1
  document = {"status": "ok", **result.as_dict()}
  if args.result_file is not None:
    args.result_file.write_text(
      json.dumps(document, sort_keys=True) + "\n",
      encoding="utf-8",
    )
  print(json.dumps(document, sort_keys=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
