import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest


AUTOMATION = Path(__file__).parents[1] / "automation"


@pytest.mark.parametrize("distribution", ["python", "module_federation"])
def test_documentation_targets_the_saved_native_release(tmp_path, distribution):
  project = tmp_path / "extensions/mail"
  project.mkdir(parents=True)
  (project / "pyproject.toml").write_text(
    '[project]\nversion="0.4.0"\n[tool.inkcre-extension]\nname="inkcre/mail"\n'
  )
  for scope in ["global", "python"]:
    config = project / f"docs/{scope}/.vitepress/config.mts"
    config.parent.mkdir(parents=True)
    config.write_text("export default {}")
  registry = tmp_path / "registry"
  registry.mkdir()
  (registry / "global.json").write_text(
    json.dumps(
      {
        "name": "inkcre/mail",
        "version": "0.9.0",
        "distribution": distribution,
        "scope": "global",
        "etag": "previous-global",
      }
    )
  )
  commands = tmp_path / "bin"
  commands.mkdir()
  stub = commands / "stub"
  stub.write_text(
    "#!/usr/bin/env python3\n"
    + textwrap.dedent("""\
      import json, os, sys
      from pathlib import Path
      command = Path(sys.argv[0]).name
      args = sys.argv[1:]
      registry = Path(os.environ["REGISTRY_FIXTURE"])
      with Path(os.environ["COMMAND_LOG"]).open("a") as log:
          log.write(json.dumps([command, *args]) + "\\n")
      def option(name):
          return args[args.index(name) + 1]
      if command == "pdm":
          action = args[3]
          if action == "show":
              sets = []
              for record in registry.glob("*.json"):
                  saved = json.loads(record.read_text())
                  if all(saved[key] == option("--" + key)
                         for key in ["name", "version", "distribution"]):
                      sets.append(saved)
              print(json.dumps({"sets": sets}))
          elif action == "pack":
              output = Path(option("--output"))
              output.mkdir()
              keys = ["name", "version", "scope", "distribution"]
              candidate = {key: option("--" + key) for key in keys}
              candidate["precondition"] = (
                  option("--if-match") if "--if-match" in args else None
              )
              candidate["metadata"] = {
                  "source_revision": option("--source-revision"),
                  "build_id": option("--build-id"),
              }
              (output / "candidate.json").write_text(json.dumps(candidate))
          elif action == "publish":
              candidate = Path(option("--candidate")) / "candidate.json"
              saved = json.loads(candidate.read_text())
              scope = saved["scope"]
              target = registry / (scope + ".json")
              previous = json.loads(target.read_text()) if target.exists() else {}
              if saved["precondition"] != previous.get("etag"):
                  raise SystemExit("precondition failed for " + scope)
              record = {**saved, **saved["metadata"], "snapshot_url":
                  "https://registry-docs-" + ("a" if scope == "global" else "b") * 32 +
                  ".inkcre.dev/"}
              target.write_text(json.dumps(record))
              print("{}")
          else:
              raise AssertionError(args)
      elif command not in ["pnpm", "curl"]:
          raise AssertionError(command)
      """)
  )
  stub.chmod(0o755)
  for command in ["pdm", "pnpm", "curl"]:
    (commands / command).symlink_to(stub)
  env = {
    **os.environ,
    "PATH": f"{commands}:{os.environ['PATH']}",
    "COMMAND_LOG": str(tmp_path / "commands.jsonl"),
    "REGISTRY_FIXTURE": str(registry),
    "CANDIDATE_ROOT": str(tmp_path / "candidate"),
    "EXTENSION": "mail",
    "NATIVE_DISTRIBUTION": distribution,
    "RELEASE_VERSION": "0.9.0",
    "GITHUB_REPOSITORY": "InKCre/impl-py",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_SHA": "reviewed-main",
    "GITHUB_RUN_ID": "same-run",
    "INKCRE_EXTENSION_REGISTRY_URL": "https://registry.example.test",
    "INKCRE_EXTENSION_REGISTRY_TOKEN": "test-only",
  }
  for action in ["prepare", "publish", "verify"]:
    if action == "verify":
      env.update(NATIVE_DISTRIBUTION="ignored", RELEASE_VERSION="1.0.0")
    subprocess.run(  # noqa: S603 - checked-in automation and local command spies only.
      ["bash", str(AUTOMATION / "extension_documentation.sh"), action],
      cwd=tmp_path,
      env=env,
      check=True,
      capture_output=True,
      text=True,
    )
  calls = [
    json.loads(line) for line in (tmp_path / "commands.jsonl").read_text().splitlines()
  ]
  expected_scopes = {"global", "python"} if distribution == "python" else {"global"}
  records = [json.loads(path.read_text()) for path in registry.glob("*.json")]
  assert {row["scope"] for row in records} == expected_scopes
  assert all(
    (row["name"], row["version"], row["distribution"])
    == ("inkcre/mail", "0.9.0", distribution)
    for row in records
  )
  assert {call[-1] for call in calls if call[0] == "curl"} == {
    row["snapshot_url"] for row in records
  }
