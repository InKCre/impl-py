"""Versioned native DAV content projections for info-base consumers."""

import vobject  # pyrefly: ignore[untyped-import]

from app.business.info_base.resolver import Resolver, TextProjectionContext
from app.business.info_base.resolver.label import format_label
from .schema import (
  CALENDAR,
  COLLECTION,
  CONTACT,
  LEGACY_CALENDAR,
  LEGACY_COLLECTION,
  LEGACY_CONTACT,
  PROPERTY_PREFIX,
  CollectionContent,
  GraphContent,
  PropertyLink,
  ResourceContent,
)


class LegacyCollectionResolver(
  Resolver[CollectionContent, str], rso_type=LEGACY_COLLECTION
):
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


class LegacyCalendarResolver(Resolver[ResourceContent, str], rso_type=LEGACY_CALENDAR):
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


class LegacyContactResolver(Resolver[ResourceContent, str], rso_type=LEGACY_CONTACT):
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


class CollectionResolver(LegacyCollectionResolver, rso_type=COLLECTION):
  pass


async def solve_resource(block_id: int) -> ResourceContent:
  from app.persistence.info_base.uow import graph_uow
  from .codec import PropertyValue, compose
  from .graph import semantic_value

  async with graph_uow(repeatable_read=True) as uow:
    block = await uow.blocks.get(block_id)
    if block is None:
      raise LookupError("DAV resource is missing")
    content = GraphContent.model_validate_json(block.content)
    properties = []
    for relation in await uow.relations.get(block_id, include_in=False):
      if not relation.content.startswith(PROPERTY_PREFIX):
        continue
      target = await uow.blocks.get(relation.to_)
      if target is None:
        raise LookupError("DAV property target is missing")
      properties.append(
        PropertyValue(
          PropertyLink.from_relation(relation.content), await semantic_value(target, uow)
        )
      )
    return ResourceContent(href=content.href, text=compose(content.remainder, properties))


class CalendarResolver(LegacyCalendarResolver, rso_type=CALENDAR):
  async def _get_solved_content(self, *, refresh=False, materialize_missing=True):
    return await solve_resource(self.block_id)

  async def get_label(self, *, refresh=False):
    canonical = GraphContent.model_validate_json(
      await self.get_raw_content(refresh=refresh)
    )
    parsed = vobject.readOne(canonical.remainder)
    for component in parsed.components():
      if component.name in ("VEVENT", "VTODO", "VJOURNAL"):
        return format_label(component.name, str(component.getChildValue("summary", "")))
    raise ValueError("Calendar resource has no supported component")


class ContactResolver(LegacyContactResolver, rso_type=CONTACT):
  async def _get_solved_content(self, *, refresh=False, materialize_missing=True):
    return await solve_resource(self.block_id)

  async def get_label(self, *, refresh=False):
    canonical = GraphContent.model_validate_json(
      await self.get_raw_content(refresh=refresh)
    )
    parsed = vobject.readOne(canonical.remainder)
    return format_label("contact", str(parsed.getChildValue("fn", "")))
