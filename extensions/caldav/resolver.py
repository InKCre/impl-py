"""Versioned native DAV content projections for info-base consumers."""

import vobject  # pyrefly: ignore[untyped-import]

from app.business.info_base.resolver import Resolver, TextProjectionContext
from app.business.info_base.resolver.label import format_label
from .schema import CALENDAR, COLLECTION, CONTACT, CollectionContent, ResourceContent


class CollectionResolver(Resolver[CollectionContent, str], rso_type=COLLECTION):
  async def _get_solved_content(self, *, refresh=False, materialize_missing=True):
    return CollectionContent.model_validate_json(
      await self.get_raw_content(refresh=refresh)
    )

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh=False,
    materialize_missing=True,
  ):
    content = await self._get_solved_content(refresh=refresh)
    return content.props.get("D:displayname", content.path)

  async def get_label(self, *, refresh=False):
    return format_label("DAV", await self.get_text(refresh=refresh))


class CalendarResolver(Resolver[ResourceContent, str], rso_type=CALENDAR):
  async def _get_solved_content(self, *, refresh=False, materialize_missing=True):
    return ResourceContent.model_validate_json(await self.get_raw_content(refresh=refresh))

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh=False,
    materialize_missing=True,
  ):
    return (await self._get_solved_content(refresh=refresh)).text

  async def get_label(self, *, refresh=False):
    parsed = vobject.readOne(await self.get_text(refresh=refresh))
    for component in parsed.components():
      if component.name in ("VEVENT", "VTODO", "VJOURNAL"):
        return format_label(component.name, str(component.getChildValue("summary", "")))
    raise ValueError("Calendar resource has no supported component")


class ContactResolver(Resolver[ResourceContent, str], rso_type=CONTACT):
  async def _get_solved_content(self, *, refresh=False, materialize_missing=True):
    return ResourceContent.model_validate_json(await self.get_raw_content(refresh=refresh))

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh=False,
    materialize_missing=True,
  ):
    return (await self._get_solved_content(refresh=refresh)).text

  async def get_label(self, *, refresh=False):
    parsed = vobject.readOne(await self.get_text(refresh=refresh))
    return format_label("contact", str(parsed.getChildValue("fn", "")))
