from io import BytesIO
import subprocess
import tarfile

from scripts import dev_database_provider as provider


def test_remote_build_context_carries_the_sdk_and_required_license(tmp_path):
  environment = tmp_path / "compose.env"
  environment.write_text("INKCRE_COMPOSE_PROJECT_NAME='test-context'\n")
  payload = provider._remote_payload(environment, ("__provider-check__",))
  with tarfile.open(fileobj=BytesIO(payload)) as archive:
    names = set(archive.getnames())
  assert "context/inkcre_core/__init__.py" in names
  assert "context/LICENSE" in names


def test_compose_failure_keeps_both_streams_and_redacts_credentials(tmp_path):
  environment = tmp_path / "compose.env"
  environment.write_text("POSTGRES_PASSWORD='test-only-secret'\n")
  error = subprocess.CalledProcessError(
    1,
    ["docker"],
    output=b"Actual build failure: test-only-secret",
    stderr=b"Compose warning",
  )
  diagnostic = str(
    provider._compose_failure(
      error, environment, provider.DatabaseProvider(kind="ssh", target="test-host")
    )
  )
  assert "Compose warning" in diagnostic
  assert "Actual build failure" in diagnostic
  assert "test-only-secret" not in diagnostic
  assert "[REDACTED]" in diagnostic
