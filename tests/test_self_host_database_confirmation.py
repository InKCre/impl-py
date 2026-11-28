"""Keep the self-host database mutation behind an explicit dispatch acknowledgement."""

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/automation/self_host.sh"


def _run_validation(
  tmp_path: Path, confirmation: str | None
) -> subprocess.CompletedProcess[str]:
  env = {
    **os.environ,
    "GITHUB_REF_TYPE": "branch",
    "CORE_DATABASE_PASSWORD": "c" * 32,
    "HEROKU_API_KEY": "test-only",
    "HEROKU_APP_PREFIX": "inkcre-owner",
    "JWT_SECRET": "j" * 32,
    "NEON_API_KEY": "test-only",
    "NEON_PROJECT_ID": "project-1",
    "POSTGREST_DATABASE_PASSWORD": "p" * 32,
    "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
  }
  if confirmation is None:
    env.pop("DATABASE_MAINTENANCE_CONFIRMED", None)
  else:
    env["DATABASE_MAINTENANCE_CONFIRMED"] = confirmation
  return subprocess.run(  # noqa: S603 - run the checked-in validator without provider access.
    ["bash", str(SCRIPT), "validate-heroku"],
    check=False,
    capture_output=True,
    text=True,
    env=env,
  )


def test_self_host_rejects_database_mutation_without_explicit_confirmation(
  tmp_path: Path,
) -> None:
  for confirmation in (None, "false"):
    result = _run_validation(tmp_path, confirmation)

    assert result.returncode != 0
    assert (
      "DATABASE_MAINTENANCE_CONFIRMED" in result.stderr
      or "Confirm the target" in result.stderr
    )
    assert not (tmp_path / "output.txt").exists()


def test_self_host_accepts_explicit_database_change_confirmation(tmp_path: Path) -> None:
  result = _run_validation(tmp_path, "true")

  assert result.returncode == 0, result.stderr
  output = (tmp_path / "output.txt").read_text(encoding="utf-8")
  assert "app_name=inkcre-owner-core" in output
