"""DAV resource envelopes and their graph-owned semantic properties."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LEGACY_COLLECTION = "extensions.caldav.collection.v1"
COLLECTION = "extensions.caldav.collection.v2"
LEGACY_CALENDAR = "extensions.caldav.calendar.v1"
LEGACY_CONTACT = "extensions.caldav.contact.v1"
CALENDAR = "extensions.caldav.calendar.v2"
CONTACT = "extensions.caldav.contact.v2"
MEMBER = "member"
PROPERTY_PREFIX = "caldav:property:"
RESOLVERS = (
  COLLECTION,
  CALENDAR,
  CONTACT,
  LEGACY_COLLECTION,
  LEGACY_CALENDAR,
  LEGACY_CONTACT,
)


class CollectionContent(BaseModel):
  model_config = ConfigDict(extra="forbid")
  path: str
  props: dict[str, str]
  published: bool = True


class ResourceContent(BaseModel):
  """Complete wire content, also the persisted legacy v1 contract."""

  model_config = ConfigDict(extra="forbid")
  href: str
  text: str


class GraphContent(BaseModel):
  """v2 stores only properties not owned by semantic Blocks or Relations."""

  model_config = ConfigDict(extra="forbid")
  href: str
  remainder: str


class PropertyLink(BaseModel):
  model_config = ConfigDict(extra="forbid")
  component: tuple[str, str, str] | None = None
  alarm: int | None = Field(default=None, ge=0)
  name: str
  params: dict[str, list[str]] = Field(default_factory=dict)
  group: str | None = None
  position: int = Field(ge=0)
  representation: Literal["text", "uri", "binary", "data-uri", "reference"]
  media_type: str | None = None

  def to_relation(self) -> str:
    return PROPERTY_PREFIX + self.model_dump_json()

  @classmethod
  def from_relation(cls, value: str) -> "PropertyLink":
    if not value.startswith(PROPERTY_PREFIX):
      raise ValueError("Not a DAV property relation")
    return cls.model_validate_json(value[len(PROPERTY_PREFIX) :])


@dataclass
class Resource:
  content: ResourceContent
  resolver: str
  block_id: int | None = None
  modified: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Collection:
  content: CollectionContent
  block_id: int | None = None
  items: dict[str, Resource] = field(default_factory=dict)
  modified: datetime = field(default_factory=lambda: datetime.now(UTC))
