"""Peer identity, configuration, capability advertisement, and persistence."""

import datetime
import typing
from urllib.parse import urlsplit, urlunsplit
import uuid

import pydantic
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel

from inkcre_core.schemas.ai import JSONValue


PeerRef: typing.TypeAlias = uuid.UUID
CapabilityID: typing.TypeAlias = str
PeerProtocolID: typing.TypeAlias = str


class PeerInboundInterface(pydantic.BaseModel):
  """One protocol-discriminated construction descriptor for a caller outbound."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  protocol: PeerProtocolID = pydantic.Field(min_length=1)
  parameters: dict[str, JSONValue]


class PeerCapabilityAdvertisement(pydantic.BaseModel):
  """One exact capability and its provider-owned inbound interface."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  id: CapabilityID = pydantic.Field(min_length=1)
  inbound: PeerInboundInterface


_CAPABILITIES_ADAPTER = pydantic.TypeAdapter(tuple[PeerCapabilityAdvertisement, ...])


def normalize_capability_snapshot(
  value: typing.Any,
) -> tuple[PeerCapabilityAdvertisement, ...]:
  """Validate, de-duplicate, and deterministically order one full snapshot."""
  capabilities = _CAPABILITIES_ADAPTER.validate_python(value or ())
  ids = tuple(capability.id for capability in capabilities)
  if len(ids) != len(set(ids)):
    raise ValueError("Peer capability IDs must be unique")
  return tuple(sorted(capabilities, key=lambda capability: capability.id))


class CorePeerConfig(pydantic.BaseModel):
  """impl-py-owned values stored in this Peer row's config object."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  http_public_base_url: str | None = pydantic.Field(default=None, title="Public HTTP URL")
  extension_registry_url: str | None = pydantic.Field(
    default=None, title="Extension Registry URL"
  )

  @pydantic.field_validator("http_public_base_url")
  @classmethod
  def valid_public_http_base(cls, value: str | None) -> str | None:
    if value is None:
      return None
    parts = urlsplit(value)
    if (
      parts.scheme not in {"http", "https"}
      or not parts.netloc
      or parts.username is not None
      or parts.password is not None
      or parts.query
      or parts.fragment
    ):
      raise ValueError(
        "http_public_base_url must be an absolute HTTP(S) URL without "
        "credentials, query, or fragment"
      )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

  @pydantic.field_validator("extension_registry_url")
  @classmethod
  def valid_extension_registry_origin(cls, value: str | None) -> str | None:
    if value is None or not value.strip():
      return None
    parts = urlsplit(value.strip())
    if (
      parts.scheme not in {"http", "https"}
      or not parts.netloc
      or parts.username is not None
      or parts.password is not None
      or parts.path not in {"", "/"}
      or parts.query
      or parts.fragment
    ):
      raise ValueError("extension_registry_url must be one HTTP(S) origin")
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


class PeerModel(sqlmodel.SQLModel, table=True):
  """One equal deployment Peer with a runtime-owned capability/lease snapshot."""

  __tablename__ = "peers"  # type: ignore

  id: PeerRef = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.UUID(as_uuid=True),
      primary_key=True,
      default=uuid.uuid4,
    ),
  )
  name: str = sqlmodel.Field(sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False))
  application_version: str | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  labels: list[str] = sqlmodel.Field(
    default_factory=list,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.ARRAY(sqlalchemy.Text),
      nullable=False,
      server_default=sqlalchemy.text("'{}'::text[]"),
    ),
  )
  config: dict[str, JSONValue] = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'{}'::jsonb"),
    ),
  )
  config_schema: dict[str, JSONValue] = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'{}'::jsonb"),
    ),
  )
  capabilities: list[dict[str, JSONValue]] = sqlmodel.Field(
    default_factory=list,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      nullable=False,
      server_default=sqlalchemy.text("'[]'::jsonb"),
    ),
  )
  lease_expires_at: datetime.datetime | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=True,
    ),
  )
  created_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )
  updated_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )

  def capability_snapshot(self) -> tuple[PeerCapabilityAdvertisement, ...]:
    """Validate a possibly externally edited persisted snapshot at use time."""
    return _CAPABILITIES_ADAPTER.validate_python(self.capabilities or ())
