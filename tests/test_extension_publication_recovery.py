import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/extension-publish.yml"
SCRIPT = WORKFLOW.parents[2] / "scripts/automation/extension_publication.sh"


def test_workflow_rejects_recapture_and_downloads_the_unique_saved_artifact():
  jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
  discover_id = next(
    identity
    for identity, job in jobs.items()
    if any("extension_publication.sh discover" in s.get("run", "") for s in job["steps"])
  )
  capture_id, capture = next(
    (identity, job)
    for identity, job in jobs.items()
    if any(
      "extension_publication.sh save-candidate" in s.get("run", "") for s in job["steps"]
    )
  )
  publish = next(
    job
    for job in jobs.values()
    if any(
      "extension_publication.sh restore-candidate" in s.get("run", "") for s in job["steps"]
    )
  )
  assert "github.repository == 'InKCre/impl-py'" in capture["if"]
  assert f"needs.{discover_id}.result == 'success'" in capture["if"]
  assert "github.run_attempt" not in capture["if"]
  reject_step = next(
    s for s in capture["steps"] if s.get("if") == "github.run_attempt != 1"
  )
  # GitHub scheduling is an independent workflow contract; execute its rejection
  # body too, without pretending this local test runs GitHub's scheduler.
  result = subprocess.run(  # noqa: S603 - executes the checked-in workflow guard body.
    ["bash", "-e", "-c", reject_step["run"]],
    capture_output=True,
    text=True,
    check=False,
  )
  assert result.returncode != 0
  assert "Do not re-run candidate capture" in result.stderr
  assert capture_id in publish["needs"]
  assert f"needs.{capture_id}.result == 'success'" in publish["if"]
  locate = next(
    s
    for s in publish["steps"]
    if "extension_publication.sh locate-artifact" in s.get("run", "")
  )
  download = next(
    s
    for s in publish["steps"]
    if s.get("uses", "").startswith("actions/download-artifact@")
  )
  assert download["with"]["artifact-ids"] == (
    "${{ steps." + locate["id"] + ".outputs.artifact_id }}"
  )
  assert download["with"]["merge-multiple"] is True


@pytest.mark.parametrize("selected", [False, True], ids=["unchanged", "release"])
def test_candidate_recovery_uses_saved_bytes_after_original_inputs_disappear(
  tmp_path, selected
):
  original = tmp_path / "original"
  original.mkdir()
  wheel = original / "fixture-1.0.0-py3-none-any.whl"
  wheel_bytes = b"independent candidate bytes: \x00\xff"
  wheel.write_bytes(wheel_bytes)
  metadata = original / "metadata.json"
  metadata.write_text(
    json.dumps(
      {
        "distribution": "python",
        "prepare": {"python": {"source_revision": "a" * 40, "build_id": "41"}},
      }
    )
  )
  saved = tmp_path / "saved"
  outputs = tmp_path / "outputs"
  environment = {
    **os.environ,
    "CANDIDATE_ROOT": str(saved),
    "EXTENSION": "mail",
    "GITHUB_RUN_ID": "41",
    "GITHUB_SHA": "a" * 40,
    "SELECTED": str(selected).lower(),
    "METADATA": str(metadata),
    "WHEEL": str(wheel),
    "GITHUB_OUTPUT": str(outputs),
  }

  def run(command):
    outputs.unlink(missing_ok=True)
    return subprocess.run(  # noqa: S603 - fixed local command; no publication or network.
      ["bash", str(SCRIPT), command],
      env=environment,
      capture_output=True,
      text=True,
      check=False,
    )

  capture = run("save-candidate")
  assert capture.returncode == 0, capture.stderr
  if selected:
    manifest = json.loads((saved / "candidate.json").read_text())
    assert manifest["wheel_sha256"] == hashlib.sha256(wheel_bytes).hexdigest()
  restored = tmp_path / "restored"
  shutil.copytree(saved, restored)
  shutil.rmtree(original)
  shutil.rmtree(saved)
  environment["CANDIDATE_ROOT"] = str(restored)
  recovery = run("restore-candidate")
  assert recovery.returncode == 0, recovery.stderr
  values = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
  assert values["selected"] == str(selected).lower()
  if not selected:
    assert values == {"selected": "false"}
    return
  recovered_wheel = Path(values["wheel"])
  assert recovered_wheel.read_bytes() == wheel_bytes
  assert json.loads(Path(values["metadata"]).read_text())["prepare"]["python"] == {
    "source_revision": "a" * 40,
    "build_id": "41",
  }
  # Same valid inputs, with only saved bytes changed: rejection must come from
  # the integrity check. Restoring the bytes is an independent positive control.
  recovered_wheel.write_bytes(b"replaced candidate")
  assert run("restore-candidate").returncode != 0
  assert "wheel=" not in outputs.read_text()
  recovered_wheel.write_bytes(wheel_bytes)
  assert run("restore-candidate").returncode == 0
