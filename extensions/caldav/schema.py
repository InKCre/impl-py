"""DAV graph facts; native component text remains the sole content authority."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

COLLECTION = "extensions.caldav.collection.v1"
CALENDAR = "extensions.caldav.calendar.v1"
CONTACT = "extensions.caldav.contact.v1"
MEMBER = "member"
RESOLVERS = (COLLECTION, CALENDAR, CONTACT)


class CollectionContent(BaseModel):
  model_config = ConfigDict(extra="forbid")
  path: str
  props: dict[str, str]


class ResourceContent(BaseModel):
  model_config = ConfigDict(extra="forbid")
  href: str
  text: str


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
