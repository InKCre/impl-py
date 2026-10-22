"""Exercise partial delivery reporting without provider credentials or shared state."""

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def _run(script: str, command: str, environment: dict[str, str]):
  return subprocess.run(  # noqa: S603 - runs a fixed repository script with isolated provider shims.
    ["bash", str(ROOT / "scripts/automation" / script), command],
    env=environment,
    capture_output=True,
    text=True,
    check=False,
  )


def test_failed_migration_keeps_observed_heads_and_reports_partial_delivery(tmp_path):
  binaries = tmp_path / "bin"
  binaries.mkdir()
  observed = {
    "status": "error",
    "contract": {"revision": "candidate-contract"},
    "migration": {"current": ["removed-revision"], "expected": ["candidate-head"]},
    "database": {"status": "error"},
  }
  docker = binaries / "docker"
  docker.write_text(
    "#!/bin/sh\n"
    'case "$*" in\n'
    f"  *' db ready '*) printf '%s\\n' '{json.dumps(observed)}' ;;\n"
    "  *' db init '*) echo 'missing revision' >&2; exit 17 ;;\n"
    "  *) echo 'unexpected provider operation' >&2; exit 88 ;;\n"
    "esac\n"
  )
  docker.chmod(0o700)
  heroku = binaries / "heroku"
  releases = json.dumps(
    [{"version": 7, "status": "succeeded", "description": "Previous web"}]
  )
  heroku.write_text(f"#!/bin/sh\nprintf '%s\\n' '{releases}'\n")
  heroku.chmod(0o700)
  output = tmp_path / "outputs"
  environment = {
    **os.environ,
    "PATH": f"{binaries}:{os.environ['PATH']}",
    "GITHUB_OUTPUT": str(output),
    "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    "HEAD_SHA": "a" * 40,
    "PR_NUMBER": "123",
    "CORE_DATABASE_PASSWORD": "secret-not-for-report",
    "POSTGREST_DATABASE_PASSWORD": "secret-not-for-report",
    "MIGRATION_DATABASE_URL": "secret-not-for-report",
    "SOURCE_DATABASE_URL": "secret-not-for-report",
  }
  failed = _run("preview_delivery.sh", "converge-database", environment)
  assert failed.returncode == 17
  values = dict(line.split("=", 1) for line in output.read_text().splitlines())
  assert json.loads(values["database_before"])["migration"] == observed["migration"]
  assert json.loads(values["database_after"])["migration"] == observed["migration"]
  assert "runtime_database_url" not in values

  runtime_report = _run(
    "preview_delivery.sh",
    "summarize",
    {
      **environment,
      "DATABASE_BEFORE": values["database_before"],
      "DATABASE_AFTER": values["database_after"],
      "DATABASE_RESULT": "failure",
      "RELEASE_RESULT": "skipped",
      "ADVERTISEMENT_RESULT": "skipped",
      "PROBE_RESULT": "skipped",
    },
  )
  assert runtime_report.returncode == 0, runtime_report.stderr
  candidate_report = _run(
    "preview_workflow.sh",
    "summarize",
    {
      **environment,
      "REGISTRY_RESULT": "success",
      "REGISTRY_SNAPSHOT": "https://snapshot.pages.dev",
      "DELIVERY_RESULT": "failure",
    },
  )
  assert candidate_report.returncode == 0, candidate_report.stderr
  summary = (tmp_path / "summary").read_text()
  assert "数据库初始化：`failure`" in summary
  assert "进程 release：`skipped`" in summary
  assert "removed-revision" in summary
  assert '"version":7' in summary
  assert "https://snapshot.pages.dev" in summary
  assert "secret-not-for-report" not in summary + failed.stdout + failed.stderr
