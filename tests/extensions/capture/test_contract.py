import json
from pathlib import Path

import pytest

from extensions.capture.schema import CaptureRequest
from extensions.capture.submit import InvalidCapture, prepare_capture


def test_shared_payload_fingerprints():
  fixtures = json.loads((Path(__file__).parent / "fixtures/capture-v1.json").read_text())
  for fixture in fixtures:
    request = CaptureRequest.model_validate(fixture["payload"])
    assert prepare_capture(request).digest == fixture["digest"]
    assert (
      prepare_capture(request.model_copy(update={"annotation": "不同的意图"})).digest
      != fixture["digest"]
    )


def test_material_limit_counts_utf8_bytes_across_materials_and_annotation():
  fixture = json.loads((Path(__file__).parent / "fixtures/capture-v1.json").read_text())[0][
    "payload"
  ]
  fixture["materials"] = [{"kind": "text", "text": "x" * (32 * 1024 * 1024 - 3)}]
  fixture["annotation"] = "中"
  prepare_capture(CaptureRequest.model_validate(fixture))
  fixture["annotation"] = "中文"
  with pytest.raises(InvalidCapture, match="32 MiB"):
    prepare_capture(CaptureRequest.model_validate(fixture))
