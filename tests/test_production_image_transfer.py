"""Keep exact Heroku image transfers ahead of a maintenance migration."""

import json
import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/automation/production_delivery.sh"


def test_transfer_images_pushes_only_the_exact_process_tags(tmp_path: Path) -> None:
  bin_directory = tmp_path / "bin"
  bin_directory.mkdir()
  calls_path = tmp_path / "docker-calls.jsonl"
  docker = bin_directory / "docker"
  docker.write_text(
    "#!/usr/bin/env python3\n"
    "import json, os, sys\n"
    "from pathlib import Path\n"
    "args = sys.argv[1:]\n"
    "if not (args and args[0] == 'login'):\n"
    "  with Path(os.environ['DOCKER_CALLS']).open('a') as calls:\n"
    "    calls.write(json.dumps(args) + '\\n')\n"
    "if args and args[0] == 'login':\n"
    "  sys.stdin.read()\n"
    "  raise SystemExit(0)\n"
    "if args[:2] == ['image', 'inspect']:\n"
    "  image = args[-1]\n"
    "  if (image.startswith('registry.heroku.com/') and\n"
    "      image == os.environ.get('MISMATCH_REMOTE')):\n"
    "    print('sha256:wrong-content')\n"
    "  else:\n"
    "    print('sha256:source-content')\n"
    "  raise SystemExit(0)\n"
    "",
    encoding="utf-8",
  )
  docker.chmod(0o755)
  test_credential = "test-heroku-token-must-not-appear"
  result = subprocess.run(  # noqa: S603 - execute the checked-in script with a local CLI stub.
    ["bash", str(SCRIPT), "transfer-images"],
    check=False,
    capture_output=True,
    text=True,
    env={
      **os.environ,
      "PATH": f"{bin_directory}:{os.environ['PATH']}",
      "APP_NAME": "inkcre-owner-core",
      "HEROKU_API_KEY": test_credential,
      "HEAD_SHA": "a" * 40,
      "POSTGREST_APP_NAME": "inkcre-owner-postgrest",
      "DOCKER_CALLS": str(calls_path),
    },
  )

  assert result.returncode == 0, result.stderr
  assert test_credential not in result.stdout + result.stderr
  calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
  assert [call for call in calls if call[0] == "tag"] == [
    [
      "tag",
      f"inkcre-production-web:{'a' * 40}",
      "registry.heroku.com/inkcre-owner-core/web",
    ],
    [
      "tag",
      f"inkcre-production-release:{'a' * 40}",
      "registry.heroku.com/inkcre-owner-core/release",
    ],
    [
      "tag",
      f"inkcre-production-postgrest:{'a' * 40}",
      "registry.heroku.com/inkcre-owner-postgrest/web",
    ],
  ]
  for operation in ("push", "pull"):
    assert {tuple(call) for call in calls if call[0] == operation} == {
      (operation, "registry.heroku.com/inkcre-owner-core/web"),
      (operation, "registry.heroku.com/inkcre-owner-core/release"),
      (operation, "registry.heroku.com/inkcre-owner-postgrest/web"),
    }


@pytest.mark.parametrize(
  "process",
  ["inkcre-owner-core/web", "inkcre-owner-core/release", "inkcre-owner-postgrest/web"],
)
def test_release_refuses_a_remote_tag_with_different_image_content(
  tmp_path: Path, process: str
) -> None:
  bin_directory = tmp_path / "bin"
  bin_directory.mkdir()
  docker = bin_directory / "docker"
  docker.write_text(
    "#!/usr/bin/env python3\n"
    "import os, sys\n"
    "args = sys.argv[1:]\n"
    "if args[:2] == ['image', 'inspect']:\n"
    "  content = (\n"
    "    'sha256:wrong-content' if args[-1] == os.environ['MISMATCH_REMOTE']\n"
    "    else 'sha256:source-content'\n"
    "  )\n"
    "  print(content)\n"
    "elif args[0] == 'pull':\n"
    "  raise SystemExit(0)\n"
    "else:\n"
    "  raise SystemExit('unexpected Docker command')\n",
    encoding="utf-8",
  )
  docker.chmod(0o755)
  result = subprocess.run(  # noqa: S603 - exercise the checked-in verifier with a local CLI stub.
    ["bash", str(SCRIPT), "verify-transferred-images"],
    check=False,
    capture_output=True,
    text=True,
    env={
      **os.environ,
      "PATH": f"{bin_directory}:{os.environ['PATH']}",
      "APP_NAME": "inkcre-owner-core",
      "HEAD_SHA": "a" * 40,
      "POSTGREST_APP_NAME": "inkcre-owner-postgrest",
      "MISMATCH_REMOTE": f"registry.heroku.com/{process}",
    },
  )

  assert result.returncode != 0
  assert "does not match source image" in result.stderr
