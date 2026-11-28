"""Do not migrate an existing self-host database while its web processes are running."""

import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/automation/production_delivery.sh"


def _run(
  directory: Path, active_app: str | None = None
) -> subprocess.CompletedProcess[str]:
  bin_directory = directory / "bin"
  bin_directory.mkdir()
  heroku = bin_directory / "heroku"
  heroku.write_text(
    "#!/usr/bin/env python3\n"
    "import json, os, sys\n"
    "args = sys.argv[1:]\n"
    "app = args[args.index('--app') + 1]\n"
    "if args[0:1] == ['apps:info']:\n"
    "  print(json.dumps({'app': {'name': app}}))\n"
    "elif args[0:1] == ['ps']:\n"
    "  dynos = ([{'type': 'web', 'state': 'up'}]\n"
    "           if app == os.environ.get('ACTIVE_APP') else [])\n"
    "  print(json.dumps(dynos))\n"
    "else:\n"
    "  raise SystemExit('unexpected Heroku mutation or command')\n",
    encoding="utf-8",
  )
  heroku.chmod(0o755)
  env = {
    **os.environ,
    "PATH": f"{bin_directory}:{os.environ['PATH']}",
    "HEROKU_API_KEY": "test-only",
    "APP_NAME": "inkcre-owner-core",
    "POSTGREST_APP_NAME": "inkcre-owner-postgrest",
  }
  if active_app is not None:
    env["ACTIVE_APP"] = active_app
  return subprocess.run(  # noqa: S603 - run the checked-in script with a local Heroku CLI stub.
    ["bash", str(SCRIPT), "require-stopped-apps"],
    check=False,
    capture_output=True,
    text=True,
    env=env,
  )


@pytest.mark.parametrize("active_app", ["inkcre-owner-core", "inkcre-owner-postgrest"])
def test_existing_self_host_apps_must_be_stopped_before_migration(
  tmp_path: Path, active_app: str
) -> None:
  result = _run(tmp_path, active_app=active_app)

  assert result.returncode != 0
  assert "still has dynos" in result.stderr


def test_empty_self_host_formations_are_ready_for_explicit_database_change(
  tmp_path: Path,
) -> None:
  result = _run(tmp_path)

  assert result.returncode == 0, result.stderr
