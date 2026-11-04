"""Normalized Peer HTTP v1 request/response envelopes."""

import re
import typing
from urllib.parse import urlsplit

import pydantic

from inkcre_core.schemas.ai import JSONValue


PEER_HTTP_PROTOCOL = "core.peer.protocol.http.v1"
PEER_EXECUTION_HEADER = "InkCre-Peer-Execution"
PEER_NOT_EXECUTED = "not-executed"

_HTTP_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def _normalized_multimap(value: typing.Any) -> dict[str, tuple[str, ...]]:
  if value is None:
    return {}
  if not isinstance(value, dict):
    raise ValueError("normalized map must be an object")
  normalized: dict[str, tuple[str, ...]] = {}
  for raw_name, raw_values in value.items():
    if not isinstance(raw_name, str) or not raw_name:
      raise ValueError("normalized map names must be non-empty strings")
    name = raw_name.lower()
    if name in normalized:
      raise ValueError(f"normalized map contains duplicate name: {name}")
    if not isinstance(raw_values, (list, tuple)) or not all(
      isinstance(item, str) for item in raw_values
    ):
      raise ValueError("normalized map values must be string arrays")
    normalized[name] = tuple(raw_values)
  return normalized


class PeerProtocolRequest(pydantic.BaseModel):
  """One protocol payload; query, headers, and JSON body may coexist."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  query: dict[str, tuple[str, ...]] = pydantic.Field(default_factory=dict)
  headers: dict[str, tuple[str, ...]] = pydantic.Field(default_factory=dict)
  body: JSONValue = None

  @pydantic.field_validator("query", "headers", mode="before")
  @classmethod
  def normalized_maps(cls, value: typing.Any) -> dict[str, tuple[str, ...]]:
    return _normalized_multimap(value)


class PeerProtocolResponse(pydantic.BaseModel):
  """One executed HTTP response projected back into the normalized protocol."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  status: int = pydantic.Field(ge=100, le=599)
  headers: dict[str, tuple[str, ...]] = pydantic.Field(default_factory=dict)
  body: JSONValue = None

  @pydantic.field_validator("headers", mode="before")
  @classmethod
  def normalized_headers(cls, value: typing.Any) -> dict[str, tuple[str, ...]]:
    return _normalized_multimap(value)


class PeerHTTPInboundParameters(pydantic.BaseModel):
  """Static parameters published by one HTTP business inbound."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  method: str
  url: str

  @pydantic.field_validator("method")
  @classmethod
  def valid_method(cls, value: str) -> str:
    method = value.upper()
    if not _HTTP_TOKEN.fullmatch(method):
      raise ValueError("HTTP method must be one valid token")
    return method

  @pydantic.field_validator("url")
  @classmethod
  def valid_absolute_url(cls, value: str) -> str:
    parts = urlsplit(value)
    if (
      parts.scheme not in {"http", "https"}
      or not parts.netloc
      or parts.username is not None
      or parts.password is not None
      or parts.fragment
    ):
      raise ValueError(
        "Peer HTTP inbound URL must be absolute HTTP(S) without credentials or fragment"
      )
    return value
