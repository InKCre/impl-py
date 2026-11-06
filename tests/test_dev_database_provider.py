import json
from io import BytesIO
import tarfile

from scripts import dev_database_provider as provider


def test_schema5_machine_override_selects_the_declared_ssh_provider(tmp_path, monkeypatch):
  base = tmp_path / "svc.json"
  local = tmp_path / "svc.local.json"
  base.write_text(json.dumps({"schema_version": 5, "dev": {"resources": {}}}))
  local.write_text(
    json.dumps(
      {
        "schema_version": 5,
        "dev": {
          "resources": {
            "database": {
              "actions": {
                "ensure": {
                  "env": {
                    "INKCRE_DATABASE_PROVIDER": "ssh",
                    "INKCRE_DATABASE_SSH_TARGET": "test-host",
                    "INKCRE_DATABASE_SSH_DOCKER_BIN": "/usr/bin/docker",
                  }
                }
              }
            }
          }
        },
      }
    )
  )
  monkeypatch.setattr(provider, "BASE_CONFIG", base)
  monkeypatch.setattr(provider, "LOCAL_CONFIG", local)
  for name in (
    "INKCRE_DATABASE_PROVIDER",
    "INKCRE_DATABASE_SSH_TARGET",
    "INKCRE_DATABASE_SSH_DOCKER_BIN",
  ):
    monkeypatch.delenv(name, raising=False)
  actual = provider.resolve_database_provider()
  assert actual.kind == "ssh"
  assert actual.target == "test-host"
  assert actual.docker_bin == "/usr/bin/docker"


def test_remote_build_context_carries_the_sdk_and_required_license(tmp_path):
  environment = tmp_path / "compose.env"
  environment.write_text("INKCRE_COMPOSE_PROJECT_NAME='test-context'\n")
  payload = provider._remote_payload(environment, ("__provider-check__",))
  with tarfile.open(fileobj=BytesIO(payload)) as archive:
    names = set(archive.getnames())
  assert "context/inkcre_core/__init__.py" in names
  assert "context/LICENSE" in names
