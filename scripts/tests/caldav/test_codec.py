"""The same authored graph fixture is consumed by the Python and Web distributions."""

import base64
import json
from pathlib import Path

import pytest

from extensions.caldav.codec import PropertyValue, compose, extract, normalize
from extensions.caldav.schema import PropertyLink

SAMPLES = json.loads((Path(__file__).parent / "fixtures/semantic-graph.json").read_text())


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["kind"])
def test_native_graph_roundtrip_preserves_components_parameters_and_bytes(sample):
  properties = [
    PropertyValue(
      PropertyLink.model_validate(p["link"]),
      base64.b64decode(p["value"]) if p["binary"] else p["value"],
    )
    for p in sample["properties"]
  ]
  text = compose(sample["remainder"], properties)
  assert text == sample["text"]
  assert normalize(text) == text
  remainder, extracted = extract(text)
  assert remainder == sample["remainder"]

  def key(field):
    return json.dumps(field.link.model_dump(), sort_keys=True)

  assert sorted(extracted, key=key) == sorted(properties, key=key)
  with pytest.raises(ValueError, match="both native and graph authority"):
    compose(text, properties)


def test_icalendar_binary_and_uri_do_not_accumulate_encoding():
  text = "\r\n".join(
    [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//test//EN",
      "BEGIN:VEVENT",
      "UID:binary",
      "DTSTAMP:20261021T000000Z",
      "DTSTART:20261021T010000Z",
      "ATTACH;VALUE=BINARY;ENCODING=BASE64;FMTTYPE=application/octet-stream:AP8BAg==",
      "URL:https://example.invalid/a,b;c",
      "END:VEVENT",
      "END:VCALENDAR",
      "",
    ]
  )
  for _ in range(3):
    remainder, fields = extract(text)
    assert next(p.value for p in fields if p.link.name == "ATTACH") == b"\x00\xff\x01\x02"
    assert (
      next(p.value for p in fields if p.link.name == "URL")
      == "https://example.invalid/a,b;c"
    )
    text = compose(remainder, fields)
  assert "URL:https://example.invalid/a,b;c" in text
