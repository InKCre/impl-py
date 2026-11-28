"""Exercise the maintenance backup command with a disposable Neon API stub."""

import json
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/automation/production_maintenance.sh"


def _write_stubs(tmp_path: Path) -> tuple[Path, Path]:
  bin_directory = tmp_path / "bin"
  bin_directory.mkdir()
  calls_path = tmp_path / "calls.jsonl"
  docker = bin_directory / "docker"
  docker.write_text(
    '#!/usr/bin/env sh\nprintf "%s\\n" "${FIXTURE_LSN:-0/AB12CD34}"\n',
    encoding="utf-8",
  )
  docker.chmod(0o755)
  curl = bin_directory / "curl"
  curl.write_text(
    """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
method = args[args.index('--request') + 1] if '--request' in args else 'GET'
url = args[-1]
output_file = args[args.index('--output') + 1] if '--output' in args else None
data = args[args.index('--data') + 1] if '--data' in args else None
record = {'method': method, 'url': url, 'data': data}
with Path(os.environ['NEON_CALLS']).open('a') as calls:
  calls.write(json.dumps(record) + '\\n')

if url.endswith('/projects/project-1'):
  limit = int(os.environ.get('BRANCH_LIMIT', '10'))
  body = {'project': {'owner': {'branches_limit': limit}}}
elif url.endswith('/branches?limit=100'):
  body = {'branches': [{'id': 'br-production', 'name': 'production'}]}
elif method == 'POST' and url.endswith('/branches'):
  if int(os.environ['CREATE_STATUS']) != 201:
    body = {'code': 'quota_exceeded', 'message': 'branch request rejected'}
  else:
    body = {
      'branch': {
        'id': 'br-backup',
        'name': 'core-py-maintenance-41',
        'parent_id': 'br-production',
        'parent_lsn': '0/AB12CD34',
      },
      'operations': [{'id': 'op-1'}],
    }
elif url.endswith('/operations/op-1'):
  body = {'operation': {'status': 'finished'}}
elif url.endswith('/branches/br-backup'):
  body = {
    'branch': {
      'id': 'br-backup',
      'name': 'core-py-maintenance-41',
      'parent_id': 'br-production',
      'parent_lsn': '0/AB12CD34',
      'default': False,
    }
  }
elif url.endswith('/endpoints?limit=100'):
  body = {'endpoints': []}
else:
  raise SystemExit('unexpected Neon API request: ' + url)

if output_file:
  Path(output_file).write_text(json.dumps(body))
  print(os.environ['CREATE_STATUS'] if method == 'POST' else '200')
else:
  print(json.dumps(body))
""",
    encoding="utf-8",
  )
  curl.chmod(0o755)
  return bin_directory, calls_path


def _environment(tmp_path: Path, bin_directory: Path, calls_path: Path) -> dict[str, str]:
  return {
    **os.environ,
    "PATH": f"{bin_directory}:{os.environ['PATH']}",
    "NEON_API_KEY": "test-only-secret",
    "NEON_BRANCH_ID": "br-production",
    "NEON_PROJECT_ID": "project-1",
    "GITHUB_RUN_ID": "41",
    "HEAD_SHA": "a" * 40,
    "MIGRATION_DATABASE_URL": "postgresql://owner:secret@db.example/neondb",
    "GITHUB_OUTPUT": str(tmp_path / "github-output.txt"),
    "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    "NEON_CALLS": str(calls_path),
    "CREATE_STATUS": "201",
    "BRANCH_LIMIT": "10",
  }


def test_backup_branch_uses_exact_lsn_without_compute_or_expiry(tmp_path: Path) -> None:
  bin_directory, calls_path = _write_stubs(tmp_path)
  result = subprocess.run(  # noqa: S603 - execute the checked-in script with a local API stub.
    ["bash", str(SCRIPT), "backup-branch"],
    check=False,
    capture_output=True,
    text=True,
    env=_environment(tmp_path, bin_directory, calls_path),
  )

  assert result.returncode == 0, result.stderr
  requests = [
    json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()
  ]
  create_request = next(request for request in requests if request["method"] == "POST")
  body = json.loads(create_request["data"])
  assert create_request["url"].endswith("/projects/project-1/branches")
  assert body == {
    "branch": {
      "name": "core-py-maintenance-41",
      "parent_id": "br-production",
      "parent_lsn": "0/AB12CD34",
    }
  }
  assert any(request["url"].endswith("/operations/op-1") for request in requests)
  assert any(request["url"].endswith("/branches/br-backup") for request in requests)
  assert any(request["url"].endswith("/endpoints?limit=100") for request in requests)
  output = Path(_environment(tmp_path, bin_directory, calls_path)["GITHUB_OUTPUT"])
  assert "backup_branch_id=br-backup" in output.read_text(encoding="utf-8")
  assert "backup_branch_lsn=0/AB12CD34" in output.read_text(encoding="utf-8")
  summary = Path(_environment(tmp_path, bin_directory, calls_path)["GITHUB_STEP_SUMMARY"])
  assert "no compute endpoint or expiration" in summary.read_text(encoding="utf-8")


def test_backup_branch_reports_neon_error_without_exposing_request_credentials(
  tmp_path: Path,
) -> None:
  bin_directory, calls_path = _write_stubs(tmp_path)
  environment = _environment(tmp_path, bin_directory, calls_path)
  environment["CREATE_STATUS"] = "422"
  result = subprocess.run(  # noqa: S603 - execute the checked-in script with a local API stub.
    ["bash", str(SCRIPT), "backup-branch"],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
  )

  assert result.returncode == 1
  assert "HTTP 422" in result.stderr
  assert "quota_exceeded" in result.stderr
  assert "branch request rejected" in result.stderr
  assert "test-only-secret" not in result.stderr
  assert "postgresql://owner:secret" not in result.stderr


def test_backup_branch_requires_capacity_for_backup_and_preserved_recovery(
  tmp_path: Path,
) -> None:
  bin_directory, calls_path = _write_stubs(tmp_path)
  environment = _environment(tmp_path, bin_directory, calls_path)
  environment["BRANCH_LIMIT"] = "2"
  result = subprocess.run(  # noqa: S603 - execute the checked-in script with a local API stub.
    ["bash", str(SCRIPT), "backup-branch"],
    check=False,
    capture_output=True,
    text=True,
    env=environment,
  )

  assert result.returncode == 1
  assert (
    "reserve both the maintenance backup and a preserved recovery branch" in result.stderr
  )
  requests = [
    json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()
  ]
  assert not any(request["method"] == "POST" for request in requests)


def test_backup_branch_rejects_invalid_database_position_before_contacting_neon(
  tmp_path: Path,
) -> None:
  binaries, calls = _write_stubs(tmp_path)
  environment = _environment(tmp_path, binaries, calls)
  environment["FIXTURE_LSN"] = "not/a-position"
  result = subprocess.run(  # noqa: S603 - checked-in command and local transports.
    ["bash", str(SCRIPT), "backup-branch"],
    env=environment,
    capture_output=True,
    text=True,
    check=False,
  )
  assert result.returncode != 0
  assert "invalid WAL flush LSN" in result.stderr
  assert not calls.exists()
