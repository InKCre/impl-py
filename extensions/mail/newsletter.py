"""Conservative, offline reading projections; the stored message stays intact."""

from __future__ import annotations

import re
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag


MAILING_LIST_RESOLVER = "extensions.mail.mailing_list.v1"
NEWSLETTER_HTML_RESOLVER = "extensions.mail.newsletter_html.v1"
NEWSLETTER_TEXT_RESOLVER = "extensions.mail.newsletter_text.v1"
HTML_BODY_RESOLVERS = {"core.html.v1", NEWSLETTER_HTML_RESOLVER}
TEXT_BODY_RESOLVERS = {"core.text.v1", NEWSLETTER_TEXT_RESOLVER}

_CONTROL = re.compile(
  r"(?:unsubscribe(?: from (?:this list|these emails))?|"
  r"manage (?:your )?(?:email )?(?:preferences|subscription)|"
  r"(?:email|subscription) preferences|"
  r"update your email preferences or unsubscribe here|"
  r"click here to unsubscribe\.?|read online|"
  r"share (?:on|via) (?:facebook|twitter|x|threads|linkedin)|"
  r"view (?:this email |this message )?in (?:your )?browser|"
  r"退订|取消订阅|管理订阅|管理订阅偏好|在浏览器中(?:查看|阅读))",
  re.IGNORECASE,
)
_HIDDEN_STYLE = re.compile(
  r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden|"
  r"opacity\s*:\s*0(?:\.0+)?|max-height\s*:\s*0(?:px)?)\s*(?:!important\s*)?(?:;|$)",
  re.IGNORECASE,
)
_TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_SIGNATURE_PARAMETERS = {"signature", "sig", "x-amz-signature", "x-goog-signature"}


def clean_link(value: str) -> str:
  """Drop only known analytics keys without re-encoding signed/functional values."""
  try:
    url = urlsplit(value)
  except ValueError:
    return value
  if url.scheme.lower() not in {"http", "https"} or not url.netloc:
    return value
  keys = {unquote_plus(item.partition("=")[0]).lower() for item in url.query.split("&")}
  if keys & _SIGNATURE_PARAMETERS:
    return value
  kept = []
  for item in url.query.split("&"):
    key = unquote_plus(item.partition("=")[0]).lower()
    if not key.startswith("utm_") and key not in _TRACKING_PARAMETERS:
      kept.append(item)
  query = "&".join(kept)
  return value if query == url.query else urlunsplit(url._replace(query=query))


def clean_newsletter_html(source: str) -> str:
  soup = BeautifulSoup(source, "html.parser")
  for element in list(soup.find_all(True)):
    if not isinstance(element, Tag) or element.parent is None:
      continue
    style = str(element.get("style", ""))
    hidden = (
      element.has_attr("hidden")
      or str(element.get("aria-hidden", "")).lower() == "true"
      or _HIDDEN_STYLE.search(style)
    )
    pixel = element.name == "img" and any(
      re.fullmatch(r"[01](?:px)?", str(element.get(size, "")).strip())
      for size in ("width", "height")
    )
    if element.name in {"head", "script", "style", "template"} or hidden or pixel:
      element.decompose()
      continue
    label = element.get_text(" ", strip=True)
    if element.name == "a" and not label:
      label = " ".join(str(image.get("alt", "")) for image in element.find_all("img"))
    label = re.sub(r"\s+", " ", label).strip()
    if element.name in {"a", "p"} and _CONTROL.fullmatch(label):
      # A control-only paragraph may label a link merely "here"; never remove its table.
      element.decompose()
    elif element.name == "a" and element.has_attr("href"):
      element["href"] = clean_link(str(element["href"]))
  return (soup.body or soup).decode_contents().strip()


def clean_newsletter_text(source: str) -> str:
  lines = []
  for line in source.splitlines():
    label = line.strip()
    if _CONTROL.fullmatch(label):
      continue
    control_link = re.fullmatch(r"(.+?)\s*(?::\s*|\s+)(https?://\S+)", label)
    if control_link and _CONTROL.fullmatch(control_link[1].strip()):
      continue
    lines.append(re.sub(r"https?://[^\s<>]+", lambda match: clean_link(match[0]), line))
  return "\n".join(lines).strip()
