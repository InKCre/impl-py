"""Split native properties from their values; compose DAV solely from current facts."""

import base64
from dataclasses import dataclass
import mimetypes
from typing import Any
from urllib.parse import unquote_to_bytes

import vobject  # pyrefly: ignore[untyped-import]

from .schema import PropertyLink

TEXT_PROPERTIES = frozenset({"TEL", "EMAIL", "URL", "NOTE", "DESCRIPTION"})
MEDIA_PROPERTIES = frozenset({"PHOTO", "LOGO", "SOUND", "ATTACH"})
CALENDAR_COMPONENTS = frozenset({"VEVENT", "VTODO", "VJOURNAL"})


@dataclass
class PropertyValue:
  link: PropertyLink
  value: str | bytes


def components(document: Any) -> dict[tuple[str, str, str] | None, Any]:
  if document.name == "VCARD":
    return {None: document}
  result: dict[tuple[str, str, str] | None, Any] = {}
  for component in document.components():
    if component.name not in CALENDAR_COMPONENTS:
      continue
    recurrence = component.contents.get("recurrence-id", [])
    key = (
      component.name,
      str(component.getChildValue("uid", "")),
      recurrence[0].serialize().strip() if recurrence else "",
    )
    if key in result:
      raise ValueError("Duplicate DAV component identity")
    result[key] = component
  return result


def resource_uid(text: str) -> str:
  parsed = vobject.readOne(text)
  if parsed.name == "VCARD":
    return str(parsed.getChildValue("uid", ""))
  uids = {key[1] for key in components(parsed) if key is not None}
  if len(uids) != 1:
    raise ValueError("DAV calendar resource must have exactly one UID")
  return uids.pop()


def declared_media_type(prop: Any) -> str | None:
  for name in ("FMTTYPE", "MEDIATYPE"):
    if prop.params.get(name):
      return prop.params[name][0].lower()
  if prop.name in MEDIA_PROPERTIES:
    for kind in prop.params.get("TYPE", []):
      if "/" in kind:
        return kind.lower()
      guessed = mimetypes.guess_type("file." + kind.lower())[0]
      if guessed:
        return guessed
  return None


def extract(text: str) -> tuple[str, list[PropertyValue]]:
  document = vobject.readOne(text)
  properties = []
  targets: list[tuple[tuple[str, str, str] | None, int | None, Any]] = []
  for key, component in components(document).items():
    targets.append((key, None, component))
    targets.extend(
      (key, index, alarm)
      for index, alarm in enumerate(component.contents.get("valarm", []))
    )
  for key, alarm, component in targets:
    for name, values in list(component.contents.items()):
      if name.upper() not in TEXT_PROPERTIES | MEDIA_PROPERTIES | {"RELATED-TO"}:
        continue
      for position, prop in enumerate(values):
        value = prop.value
        representation = "text"
        media_type = declared_media_type(prop)
        if prop.name == "RELATED-TO":
          representation = "reference"
        elif prop.name == "URL":
          representation = "uri"
        elif prop.name in MEDIA_PROPERTIES:
          if isinstance(value, bytes):
            representation = "binary"
          elif str(value).lower().startswith("data:"):
            header, payload = str(value).split(",", 1)
            media_type = header[5:].split(";", 1)[0] or "text/plain"
            value = (
              base64.b64decode("".join(payload.split()), validate=True)
              if header.lower().endswith(";base64")
              else unquote_to_bytes(payload)
            )
            representation = "data-uri"
          elif any(v.upper() == "BASE64" for v in prop.params.get("ENCODING", [])):
            value = base64.b64decode("".join(str(value).split()), validate=True)
            representation = "binary"
          else:
            representation = "uri"
        if not isinstance(value, (str, bytes)):
          raise ValueError(f"Unsupported DAV property value: {prop.name}")
        properties.append(
          PropertyValue(
            PropertyLink(
              component=key,
              alarm=alarm,
              name=prop.name,
              params=prop.params,
              group=prop.group,
              position=position,
              representation=representation,
              media_type=media_type,
            ),
            value,
          )
        )
      del component.contents[name]
  return document.serialize(validate=False), properties


def compose(remainder: str, properties: list[PropertyValue]) -> str:
  document = vobject.readOne(remainder)
  targets = components(document)
  seen = set()

  def target_for(link):
    target = targets.get(link.component)
    if target is not None and link.alarm is not None:
      alarms = target.contents.get("valarm", [])
      target = alarms[link.alarm] if link.alarm < len(alarms) else None
    return target

  for field in properties:
    target = target_for(field.link)
    if target is not None and field.link.name.lower() in target.contents:
      raise ValueError("DAV property has both native and graph authority")
  for field in sorted(properties, key=lambda p: p.link.position):
    link = field.link
    target = target_for(link)
    if target is None:
      raise ValueError("DAV property refers to a missing component")
    identity = (link.component, link.alarm, link.name, link.position)
    if identity in seen:
      raise ValueError("Duplicate DAV property position")
    seen.add(identity)
    prop = target.add(link.name)
    prop.params = {name: list(values) for name, values in link.params.items()}
    prop.group = link.group
    value = field.value
    if link.representation in ("binary", "data-uri"):
      if not isinstance(value, bytes):
        raise ValueError("DAV binary property requires bytes")
      encoded = base64.b64encode(value).decode("ascii")
      if link.representation == "data-uri":
        value = f"data:{link.media_type or 'application/octet-stream'};base64,{encoded}"
      elif document.name != "VCARD":
        # vobject's default iCalendar TextBehavior re-encodes strings and rejects bytes.
        value = encoded
        prop.encoded = True
    if document.name != "VCARD" and link.representation in ("uri", "data-uri"):
      prop.encoded = True
    elif link.representation not in ("binary", "data-uri") and not isinstance(value, str):
      raise ValueError("DAV text property requires text")
    prop.value = value
  return document.serialize(validate=False)


def normalize(text: str) -> str:
  remainder, properties = extract(text)
  return compose(remainder, properties)
