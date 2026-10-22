import re
import subprocess
import textwrap
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/extension-publish.yml"


def test_full_rerun_fails_capture_and_publish_only_rerun_restores_saved_artifact():
  workflow = WORKFLOW.read_text(encoding="utf-8")
  capture = workflow.split("\n  capture:\n", maxsplit=1)[1].split(
    "\n  publish:\n", maxsplit=1
  )[0]
  publish = workflow.split("\n  publish:\n", maxsplit=1)[1]

  capture_job_if = re.search(r"(?m)^    if: >-\n(?P<condition>(?:      .*\n)+)", capture)
  assert capture_job_if is not None
  assert "github.repository == 'InKCre/impl-py'" in capture_job_if["condition"]
  assert "needs.discover.result == 'success'" in capture_job_if["condition"]
  assert "github.run_attempt" not in capture_job_if["condition"]

  reject_step = capture.split(
    "      - name: Reject candidate reconstruction\n", maxsplit=1
  )[1].split("      - name:", maxsplit=1)[0]
  assert "if: github.run_attempt != 1" in reject_step
  run_block = re.search(
    r"(?m)^        run: \|\n(?P<script>(?:          .*\n)+)", reject_step
  )
  assert run_block is not None
  script = textwrap.dedent(run_block["script"])

  # GitHub evaluates the step condition for a full rerun (attempt 2), then runs
  # this exact guard. Check its observable failure, not just the YAML wiring.
  result = subprocess.run(  # noqa: S603 - executes the checked-in workflow guard body.
    ["bash", "-e", "-c", script], capture_output=True, text=True, check=False
  )
  assert result.returncode != 0
  assert "Do not re-run candidate capture" in result.stderr

  assert "needs.capture.result == 'success'" in publish
  assert "Locate unique saved candidate artifact" in publish
  assert "artifact-ids: ${{ steps.artifact.outputs.artifact_id }}" in publish
  assert "merge-multiple: true" in publish
