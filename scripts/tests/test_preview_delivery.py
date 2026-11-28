"""Exercise partial delivery reporting without provider credentials or shared state."""

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _run(script: str, command: str, environment: dict[str, str]):
  return subprocess.run(  # noqa: S603 - runs a fixed repository script with isolated provider shims.
    ["bash", str(ROOT / "scripts/automation" / script), command],
    env=environment,
    capture_output=True,
    text=True,
    check=False,
  )


@pytest.mark.parametrize("artifact_mode", [False, True], ids=["current-main", "artifact"])
def test_preview_build_serves_each_controller(tmp_path, artifact_mode):
  workspace = tmp_path / "workspace"
  source = workspace / "source"
  runner_temp = tmp_path / "temp"
  artifact = runner_temp / "preview-artifact"
  calls = tmp_path / "calls"
  pages_project = "inkcre-core-py-extension-registry-preview"
  head = "a" * 40
  for tool in ("pdm", "docker"):
    command = tmp_path / tool
    command.write_text('#!/bin/sh\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$BUILD_CALLS"\n')
    command.chmod(0o700)

  environment = {
    **os.environ,
    "PATH": f"{tmp_path}:{os.environ['PATH']}",
    "BUILD_CALLS": str(calls),
    "GITHUB_WORKSPACE": str(workspace),
    "HEAD_SHA": head,
    "PAGES_PROJECT": pages_project,
    "PR_NUMBER": "123",
    "RUNNER_TEMP": str(runner_temp),
    "SOURCE_DIRECTORY": str(source),
  }
  environment.pop("ARTIFACT_DIRECTORY", None)
  preview_inputs = workspace / ".extension-preview-inputs"
  registry = workspace / ".extension-preview-registry"
  if artifact_mode:
    environment["ARTIFACT_DIRECTORY"] = str(artifact)
    preview_inputs = runner_temp / "preview-wheel-inputs"
    registry = artifact / "registry"

  for command in ("build-extensions", "build-images"):
    result = _run("preview_workflow.sh", command, environment)
    assert result.returncode == 0, result.stderr

  tags = [f"inkcre-preview-{process}:{head}" for process in ("web", "release", "postgrest")]
  docker_build = "docker build --platform linux/amd64 --provenance=false"
  expected = [
    f"pdm run build:extension-preview --output {preview_inputs}",
    f"pdm run inkcre-ext preview build --inventory {preview_inputs / 'extensions.json'} "
    f"--public-origin https://pr-123.{pages_project}.pages.dev --output {registry}",
    f"{docker_build} --target heroku-web --build-arg SOURCE_REVISION={head} "
    f"--tag {tags[0]} {source}",
    f"{docker_build} --target heroku-release --build-arg SOURCE_REVISION={head} "
    f"--tag {tags[1]} {source}",
    f"{docker_build} --file {source / 'Dockerfile.postgrest'} "
    f"--build-arg SOURCE_REVISION={head} --tag {tags[2]} {source}",
  ]
  if artifact_mode:
    archive = artifact / "images.tar"
    expected.append(f"docker image save --output {archive} {' '.join(tags)}")
  assert calls.read_text().splitlines() == expected


@pytest.mark.parametrize(
  "defect, docker_expected",
  [
    (None, True),
    ("worker", False),
    ("configuration", False),
    ("symlink", False),
    ("missing-registry-index", False),
    ("missing-images", False),
    ("missing-artifact-directory", False),
    ("wrong-source", True),
    ("wrong-platform", True),
  ],
)
def test_only_static_registry_and_exact_head_images_can_be_delivered(
  tmp_path, defect, docker_expected
):
  artifact = tmp_path / "artifact"
  registry = artifact / "registry"
  (registry / "v2").mkdir(parents=True)
  for path in ("_headers", "_redirects", "v2/extensions.json"):
    (registry / path).touch()
  (artifact / "images.tar").touch()

  match defect:
    case "worker":
      (registry / "_worker.js").mkdir()
    case "configuration":
      (registry / "wrangler.toml").touch()
    case "symlink":
      (registry / "v2/other.json").symlink_to(registry / "_headers")
    case "missing-registry-index":
      (registry / "v2/extensions.json").unlink()
    case "missing-images":
      (artifact / "images.tar").unlink()

  head = "a" * 40
  docker = tmp_path / "docker"
  docker.write_text(
    "#!/bin/sh\n"
    'printf "%s\\n" "$*" >> "$DOCKER_CALLS"\n'
    'if [ "$2" = inspect ]; then\n'
    '  printf "%s %s\\n" "$IMAGE_PLATFORM" "$IMAGE_REVISION"\n'
    "fi\n"
  )
  docker.chmod(0o700)
  calls = tmp_path / "calls"
  environment = {
    **os.environ,
    "PATH": f"{tmp_path}:{os.environ['PATH']}",
    "ARTIFACT_DIRECTORY": str(artifact),
    "HEAD_SHA": head,
    "DOCKER_CALLS": str(calls),
    "IMAGE_PLATFORM": "linux/arm64" if defect == "wrong-platform" else "linux/amd64",
    "IMAGE_REVISION": "b" * 40 if defect == "wrong-source" else head,
  }
  if defect == "missing-artifact-directory":
    del environment["ARTIFACT_DIRECTORY"]
  result = _run("preview_workflow.sh", "load-artifact", environment)

  assert (result.returncode == 0) == (defect is None), result.stderr
  assert calls.exists() == docker_expected
  if defect is None:
    commands = calls.read_text().splitlines()
    assert commands[0] == f"image load --input {artifact / 'images.tar'}"
    assert [command.split()[-1] for command in commands[1:]] == [
      f"inkcre-preview-{process}:{head}" for process in ("web", "release", "postgrest")
    ]


@pytest.mark.parametrize(
  "parent_id, allowed", [("br-preview-base", True), ("br-other", False)]
)
def test_database_resolution_requires_the_sanitized_parent(tmp_path, parent_id, allowed):
  branches = [
    {"name": "preview-base", "id": "br-preview-base"},
    {
      "name": "preview/core-py/pr-123",
      "id": "br-preview-123",
      "parent_id": parent_id,
      "expires_at": "2030-01-02T00:00:00Z",
    },
  ]
  npx = tmp_path / "npx"
  npx.write_text(
    "#!/bin/sh\n"
    'case "$*" in\n'
    f"  *' branches list '*) printf '%s\\n' '{json.dumps(branches)}' ;;\n"
    "  *' connection-string br-preview-123 '*) echo 'resolved-preview-url' ;;\n"
    "  *) exit 88 ;;\n"
    "esac\n"
  )
  npx.chmod(0o700)
  result = _run(
    "preview_delivery.sh",
    "resolve-neon",
    {
      **os.environ,
      "PATH": f"{tmp_path}:{os.environ['PATH']}",
      "GITHUB_OUTPUT": str(tmp_path / "outputs"),
      "NEON_API_KEY": "fixture-only",
      "NEON_PROJECT_ID": "fixture",
      "PR_NUMBER": "123",
    },
  )
  assert (result.returncode == 0) == allowed, result.stderr
  assert (tmp_path / "outputs").exists() == allowed


def test_database_init_uses_only_explicit_database_env_and_trusted_url_rebinding(tmp_path):
  docker = tmp_path / "docker"
  docker.write_text(
    "#!/bin/sh\n"
    'printf "%s\\n" "$*" >> "$DOCKER_CALLS"\n'
    'case "$*" in\n'
    "  *' db ready '*) printf '%s\\n' '{\"status\":\"ready\"}' ;;\n"
    "  *' db init '*) echo 'fixture database initialized' ;;\n"
    "  *) exit 88 ;;\n"
    "esac\n"
  )
  docker.chmod(0o700)
  calls = tmp_path / "calls"
  output = tmp_path / "outputs"
  head = "a" * 40
  result = _run(
    "preview_delivery.sh",
    "converge-database",
    {
      **os.environ,
      "PATH": f"{tmp_path}:{os.environ['PATH']}",
      "DOCKER_CALLS": str(calls),
      "GITHUB_OUTPUT": str(output),
      "HEAD_SHA": head,
      "CORE_DATABASE_PASSWORD": "some:password",
      "POSTGREST_DATABASE_PASSWORD": "other@password",
      "MIGRATION_DATABASE_URL": "postgresql://owner:fake@direct.example/db",
      "SOURCE_DATABASE_URL": "postgresql://owner:fake@pool.example/db?sslmode=require",
    },
  )
  assert result.returncode == 0, result.stderr
  before, init, after = calls.read_text().splitlines()
  prefix = (
    "run --rm --user 10001:10001 --cap-drop ALL "
    "--security-opt no-new-privileges --entrypoint python "
  )
  ready = (
    f"{prefix}--env MIGRATION_DATABASE_URL inkcre-preview-web:{head} "
    "scripts/container.py db ready --profile runtime --json"
  )
  assert before == after == ready
  assert init == (
    f"{prefix}--env CORE_DATABASE_PASSWORD --env MIGRATION_DATABASE_URL "
    f"--env POSTGREST_DATABASE_PASSWORD inkcre-preview-web:{head} "
    "scripts/container.py db init --profile runtime --environment preview"
  )
  values = dict(line.split("=", 1) for line in output.read_text().splitlines())
  assert values["runtime_database_url"] == (
    "postgresql+psycopg://inkcre_core:some%3Apassword@pool.example/db?sslmode=require"
  )
  assert values["postgrest_database_url"] == (
    "postgresql://authenticator:other%40password@pool.example/db?sslmode=require"
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


@pytest.mark.parametrize(
  "paths,changed_files,scenario,required",
  [
    (["tests/a.py", "tests/b.py"], 2, None, "false"),
    (["tests/a.py", "app/main.py"], 2, None, "true"),
    (["tests/a.py"], 1, "rename-from-app", "true"),
    (["scripts/tests/a.py"], 1, None, "true"),
    (["tests/a.py"], 3001, None, "true"),
    (["tests/a.py"], 1, "head", "true"),
    (["tests/a.py"], 1, "base", "true"),
    (["tests/a.py"], 1, "api-failure", "true"),
    ([], 0, None, "true"),
  ],
)
def test_preview_skips_only_a_complete_stable_tests_only_diff(
  tmp_path, paths, changed_files, scenario, required
):
  pull = {
    "state": "open",
    "head": {"sha": "a" * 40, "repo": {"full_name": "InKCre/impl-py"}},
    "base": {
      "sha": "b" * 40,
      "ref": "main",
      "repo": {"full_name": "InKCre/impl-py"},
    },
    "changed_files": changed_files,
  }
  (tmp_path / "before.json").write_text(json.dumps(pull))
  if scenario in ("head", "base"):
    pull[scenario]["sha"] = "c" * 40
  (tmp_path / "after.json").write_text(json.dumps(pull))
  pages = [[{"filename": path}] for path in paths]
  if scenario == "rename-from-app":
    pages[0][0]["previous_filename"] = "app/main.py"
  (tmp_path / "files.json").write_text(json.dumps(pages))
  binary = tmp_path / "gh"
  binary.write_text(
    "#!/bin/sh\n"
    'cd "$GH_SCOPE_FIXTURE" || exit 1\n'
    'case "$*" in\n'
    '  *"/files?"*) [ "$GH_SCOPE_FAILURE" = false ] || exit 17; cat files.json ;;\n'
    "  *) if [ -e called ]; then cat after.json; "
    "else touch called; cat before.json; fi ;;\n"
    "esac\n"
  )
  binary.chmod(0o700)
  output = tmp_path / "output"
  result = _run(
    "preview_workflow.sh",
    "application-required",
    {
      **os.environ,
      "PATH": f"{tmp_path}:{os.environ['PATH']}",
      "GH_SCOPE_FIXTURE": str(tmp_path),
      "GH_SCOPE_FAILURE": str(scenario == "api-failure").lower(),
      "GH_TOKEN": "fixture-only",
      "GITHUB_REPOSITORY": "InKCre/impl-py",
      "GITHUB_OUTPUT": str(output),
      "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
      "HEAD_SHA": "a" * 40,
      "BASE_SHA": "b" * 40,
      "PR_NUMBER": "123",
    },
  )
  assert result.returncode == 0, result.stderr
  assert output.read_text() == f"required={required}\n"
