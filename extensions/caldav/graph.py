"""One DAV lock owns one graph transaction, including conditional writes."""

from contextlib import asynccontextmanager

from app.persistence.info_base.uow import GraphUnitOfWork, graph_uow
from app.schemas.info_base.block import BlockForm
from .schema import (
  CALENDAR,
  COLLECTION,
  CONTACT,
  MEMBER,
  Collection,
  CollectionContent,
  Resource,
  ResourceContent,
)


class DAVGraph:
  def __init__(self, uow: GraphUnitOfWork):
    self.uow = uow
    self.collections: dict[str, Collection] = {}
    self.original: dict[int, tuple[str, str]] = {}
    self.members: dict[tuple[int, int], int] = {}

  async def load(self, *, lock: bool = False) -> None:
    # Lock parents before memberships and resources: FK inserts must wait too.
    collections = await self.uow.blocks.get_by_resolvers((COLLECTION,), lock=lock)
    relations = await self.uow.relations.get_outgoing_many(
      tuple(block.id for block in collections if block.id is not None),
      content=MEMBER,
      lock=lock,
    )
    objects = await self.uow.blocks.get_by_resolvers((CALENDAR, CONTACT), lock=lock)
    blocks = (*collections, *objects)
    by_id: dict[int, Collection] = {}
    resources: dict[int, Resource] = {}
    for block in blocks:
      if block.id is None or block.storage is not None:
        raise ValueError("DAV graph requires persisted inline blocks")
      self.original[block.id] = (block.resolver, block.content)
      if block.resolver == COLLECTION:
        content = CollectionContent.model_validate_json(block.content)
        if content.path in self.collections:
          raise ValueError("Duplicate DAV collection path")
        collection = Collection(content, block.id, modified=block.updated_at)
        self.collections[content.path] = collection
        by_id[block.id] = collection
      else:
        resources[block.id] = Resource(
          ResourceContent.model_validate_json(block.content),
          block.resolver,
          block.id,
          block.updated_at,
        )
    assigned: set[int] = set()
    for relation in relations:
      if relation.content != MEMBER:
        continue
      resource = resources.get(relation.to_)
      collection = by_id[relation.from_]
      if resource is None or relation.to_ in assigned:
        raise ValueError("DAV membership must have one collection owner")
      if resource.content.href in collection.items:
        raise ValueError("Duplicate DAV resource href")
      expected = (
        CONTACT if collection.content.props.get("tag") == "VADDRESSBOOK" else CALENDAR
      )
      if resource.resolver != expected:
        raise ValueError("DAV resource type differs from collection")
      collection.items[resource.content.href] = resource
      collection.modified = max(collection.modified, resource.modified)
      assigned.add(relation.to_)
      if relation.id is None:
        raise ValueError("DAV membership must be persisted")
      self.members[(relation.from_, relation.to_)] = relation.id
    if assigned != resources.keys():
      raise ValueError("DAV resource has no collection owner")

  async def save(self) -> None:
    retained: set[int] = set()
    memberships: set[tuple[int, int]] = set()
    for collection in self.collections.values():
      collection.block_id = await self._save_block(
        collection.block_id, COLLECTION, collection.content.model_dump_json()
      )
      retained.add(collection.block_id)
      for resource in collection.items.values():
        resource.block_id = await self._save_block(
          resource.block_id, resource.resolver, resource.content.model_dump_json()
        )
        retained.add(resource.block_id)
        memberships.add((collection.block_id, resource.block_id))
    await self.uow.relations.delete_many(
      tuple(
        relation_id for pair, relation_id in self.members.items() if pair not in memberships
      )
    )
    for from_id, to_id in memberships - self.members.keys():
      await self.uow.relations.create(from_id, to_id, MEMBER)
    for block_id in self.original.keys() - retained:
      await self.uow.blocks.delete(block_id)

  async def _save_block(self, block_id: int | None, resolver: str, content: str) -> int:
    if block_id is None:
      block = await self.uow.blocks.create(BlockForm(resolver=resolver, content=content))
      assert block.id is not None
      return block.id
    if self.original[block_id] != (resolver, content):
      await self.uow.blocks.edit_block(block_id, content=content, resolver=resolver)
    return block_id


@asynccontextmanager
async def dav_transaction(mode: str):
  async with graph_uow(repeatable_read=mode == "r") as uow:
    await uow.blocks.acquire_transaction_lock("extensions.caldav.v1", shared=mode == "r")
    graph = DAVGraph(uow)
    await graph.load(lock=mode == "w")
    yield graph
    if mode == "w":
      await graph.save()
