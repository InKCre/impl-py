import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/automation/production_delivery.sh"


def _heroku_cli(directory: Path) -> Path:
  executable = directory / "heroku"
  executable.write_text(
    "#!/usr/bin/env python3\n"
    "import json, os, sys\n"
    "from pathlib import Path\n"
    "args = sys.argv[1:]\n"
    "with Path(os.environ['HEROKU_CALLS']).open('a') as calls:\n"
    "  calls.write(' '.join(args) + '\\n')\n"
    "app = args[args.index('--app') + 1]\n"
    "if app == os.environ.get('HEROKU_FAILED_APP'):\n"
    "  raise SystemExit(1)\n"
    "print(json.dumps({'app': {'name': app, 'web_url': f'https://{app}.example/'}}))\n",
    encoding="utf-8",
  )
  executable.chmod(0o755)
  return executable


def _run(
  directory: Path,
  *,
  failed_app: str | None = None,
) -> subprocess.CompletedProcess[str]:
  bin_directory = directory / "bin"
  bin_directory.mkdir()
  _heroku_cli(bin_directory)
  calls = directory / "heroku-calls.txt"
  output = directory / "github-output.txt"
  env = {
    **os.environ,
    "PATH": f"{bin_directory}:{os.environ['PATH']}",
    "GITHUB_OUTPUT": str(output),
    "HEROKU_API_KEY": "test-only",
    "HEROKU_APP_NAME": "inkcre-core-production",
    "POSTGREST_APP_NAME": "inkcre-postgrest-production",
    "HEROKU_CALLS": str(calls),
    "REQUIRE_EXISTING_APPS": "true",
  }
  if failed_app is not None:
    env["HEROKU_FAILED_APP"] = failed_app
  return subprocess.run(  # noqa: S603 - execute the checked-in shell workflow with a local CLI stub.
    ["bash", str(SCRIPT), "resolve-apps"],
    check=False,
    capture_output=True,
    text=True,
    env=env,
  )


def test_production_app_resolution_reads_exact_existing_app_pair(tmp_path: Path) -> None:
  result = _run(tmp_path)

  assert result.returncode == 0, result.stderr
  calls = (tmp_path / "heroku-calls.txt").read_text(encoding="utf-8")
  assert "apps:info --app inkcre-core-production --json" in calls
  assert "apps:info --app inkcre-postgrest-production --json" in calls
  assert "apps:create" not in calls
  output = (tmp_path / "github-output.txt").read_text(encoding="utf-8")
  assert "core_app_created=false" in output
  assert "postgrest_app_created=false" in output


def test_production_app_resolution_fails_closed_on_auth_or_lookup_error(
  tmp_path: Path,
) -> None:
  result = _run(tmp_path, failed_app="inkcre-core-production")

  assert result.returncode != 0
  assert "Could not read configured production Heroku app" in result.stderr
  calls = (tmp_path / "heroku-calls.txt").read_text(encoding="utf-8")
  assert "apps:create" not in calls


@pytest.mark.parametrize("unexpected_name", ["", "another-app"])
def test_production_app_resolution_rejects_wrong_app_identity(
  tmp_path: Path,
  unexpected_name: str,
) -> None:
  _heroku_cli = tmp_path / "bin"
  _heroku_cli.mkdir()
  executable = _heroku_cli / "heroku"
  executable.write_text(
    "#!/usr/bin/env python3\n"
    "import json, os, sys\n"
    "from pathlib import Path\n"
    "with Path(os.environ['HEROKU_CALLS']).open('a') as calls:\n"
    "  calls.write(' '.join(sys.argv[1:]) + '\\n')\n"
    "app = sys.argv[sys.argv.index('--app') + 1]\n"
    f"name = {unexpected_name!r}\n"
    "print(json.dumps({'app': {'name': name if app == "
    "'inkcre-core-production' else app}}))\n",
    encoding="utf-8",
  )
  executable.chmod(0o755)
  calls = tmp_path / "heroku-calls.txt"
  output = tmp_path / "github-output.txt"
  result = subprocess.run(  # noqa: S603 - execute the checked-in shell workflow with a local CLI stub.
    ["bash", str(SCRIPT), "resolve-apps"],
    check=False,
    capture_output=True,
    text=True,
    env={
      **os.environ,
      "PATH": f"{_heroku_cli}:{os.environ['PATH']}",
      "GITHUB_OUTPUT": str(output),
      "HEROKU_API_KEY": "test-only",
      "HEROKU_APP_NAME": "inkcre-core-production",
      "POSTGREST_APP_NAME": "inkcre-postgrest-production",
      "HEROKU_CALLS": str(calls),
      "REQUIRE_EXISTING_APPS": "true",
    },
  )

  assert result.returncode != 0
  assert "unexpected app identity" in result.stderr
  assert "apps:create" not in calls.read_text(encoding="utf-8")
