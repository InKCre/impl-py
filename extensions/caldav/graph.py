"""DAV publication and semantic properties in one graph transaction."""

from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.business.info_base.resolver import ResolverManager
from app.business.info_base.resolver.inspection import detect_media_type
from app.business.info_base.storage import StorageManager
from app.business.info_base.storage.postgresql import PostgreSQLBinaryStorage
from app.persistence.info_base.uow import GraphUnitOfWork, graph_uow
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.info_base.relation import RelationModel
from .codec import PropertyValue, compose, extract, resource_uid
from .schema import (
  CALENDAR,
  COLLECTION,
  CONTACT,
  LEGACY_CALENDAR,
  LEGACY_COLLECTION,
  LEGACY_CONTACT,
  MEMBER,
  PROPERTY_PREFIX,
  Collection,
  CollectionContent,
  GraphContent,
  PropertyLink,
  Resource,
  ResourceContent,
)

RESOURCE_RESOLVERS = (CALENDAR, CONTACT, LEGACY_CALENDAR, LEGACY_CONTACT)


@dataclass
class BoundProperty:
  relation: RelationModel
  field: PropertyValue


async def semantic_value(block: BlockModel, uow: GraphUnitOfWork, *, lock=False):
  if block.resolver in RESOURCE_RESOLVERS:
    text = (
      ResourceContent.model_validate_json(block.content).text
      if block.resolver in (LEGACY_CALENDAR, LEGACY_CONTACT)
      else GraphContent.model_validate_json(block.content).remainder
    )
    return resource_uid(text)
  if block.storage is None:
    if block.resolver != "core.text.v1":
      raise ValueError("DAV inline property must use the text resolver")
    return block.content
  storage = StorageManager.from_record(await uow.storage.get(block.storage))
  if not isinstance(storage, PostgreSQLBinaryStorage):
    raise ValueError("DAV embedded properties require database-owned bytes")
  return await storage.read_content(block.content, uow.storage, lock=lock)


class DAVGraph:
  def __init__(self, uow: GraphUnitOfWork):
    self.uow = uow
    self.collections: dict[str, Collection] = {}
    self.resources: dict[int, Resource] = {}
    self.original: dict[int, tuple[str, str]] = {}
    self.collection_content: dict[int, CollectionContent] = {}
    self.members: dict[tuple[int, int], int] = {}
    self.properties: dict[int, list[BoundProperty]] = {}

  async def load(self, *, lock: bool = False) -> None:
    # Root locks also block new FK references; lock dependent values before reading them.
    collections = await self.uow.blocks.get_by_resolvers(
      (COLLECTION, LEGACY_COLLECTION), lock=lock
    )
    objects = await self.uow.blocks.get_by_resolvers(RESOURCE_RESOLVERS, lock=lock)
    roots = {block.id: block for block in (*collections, *objects) if block.id is not None}
    relations = await self.uow.relations.get_outgoing_many(tuple(roots), lock=lock)
    property_relations = [r for r in relations if r.content.startswith(PROPERTY_PREFIX)]
    dependencies = {}
    for target_id in sorted({r.to_ for r in property_relations} - roots.keys()):
      block = await self.uow.blocks.get(target_id, lock=lock)
      if block is None:
        raise ValueError("DAV property target is missing")
      dependencies[target_id] = block
    values = {}
    for target_id in sorted({r.to_ for r in property_relations}):
      block = roots.get(target_id) or dependencies[target_id]
      values[target_id] = await semantic_value(block, self.uow, lock=lock)
    for relation in property_relations:
      field = PropertyValue(
        PropertyLink.from_relation(relation.content), values[relation.to_]
      )
      self.properties.setdefault(relation.from_, []).append(BoundProperty(relation, field))

    by_id = {}
    for block in roots.values():
      if block.id is None or block.storage is not None:
        raise ValueError("DAV roots require persisted inline blocks")
      self.original[block.id] = (block.resolver, block.content)
      if block.resolver in (COLLECTION, LEGACY_COLLECTION):
        content = CollectionContent.model_validate_json(block.content)
        self.collection_content[block.id] = content
        if not content.published:
          continue
        if content.path in self.collections:
          raise ValueError("Duplicate DAV collection path")
        collection = Collection(content, block.id, modified=block.updated_at)
        self.collections[content.path] = collection
        by_id[block.id] = collection
      else:
        if block.resolver in (LEGACY_CALENDAR, LEGACY_CONTACT):
          content = ResourceContent.model_validate_json(block.content)
        else:
          canonical = GraphContent.model_validate_json(block.content)
          content = ResourceContent(
            href=canonical.href,
            text=compose(
              canonical.remainder, [p.field for p in self.properties.get(block.id, [])]
            ),
          )
        self.resources[block.id] = Resource(
          content,
          CONTACT if block.resolver in (CONTACT, LEGACY_CONTACT) else CALENDAR,
          block.id,
          block.updated_at,
        )
    assigned: set[int] = set()
    for relation in relations:
      if relation.content != MEMBER or relation.from_ not in by_id:
        continue
      resource = self.resources.get(relation.to_)
      collection = by_id[relation.from_]
      if resource is None or relation.to_ in assigned:
        raise ValueError("DAV published resource must have one collection owner")
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
      assert relation.id is not None
      self.members[(relation.from_, relation.to_)] = relation.id

  async def save(self) -> None:
    memberships = set()
    active_collections = set()
    resources = dict(self.resources)
    # Establish all root identities before resolving child-before-parent references.
    for collection in self.collections.values():
      collection.block_id = await self._save_block(
        collection.block_id, COLLECTION, collection.content.model_dump_json()
      )
      active_collections.add(collection.block_id)
      for resource in collection.items.values():
        remainder, _ = extract(resource.content.text)
        resource.block_id = await self._save_block(
          resource.block_id,
          resource.resolver,
          GraphContent(href=resource.content.href, remainder=remainder).model_dump_json(),
        )
        resources[resource.block_id] = resource
        memberships.add((collection.block_id, resource.block_id))

    scopes: dict[int, dict[str, list[int]]] = {}
    for collection in self.collections.values():
      uids: dict[str, list[int]] = {}
      for resource in collection.items.values():
        assert resource.block_id is not None
        uid = resource_uid(resource.content.text)
        if uid:
          uids.setdefault(uid, []).append(resource.block_id)
      for resource in collection.items.values():
        assert resource.block_id is not None
        scopes[resource.block_id] = uids
    for block_id, resource in resources.items():
      remainder, properties = extract(resource.content.text)
      await self._save_block(
        block_id,
        resource.resolver,
        GraphContent(href=resource.content.href, remainder=remainder).model_dump_json(),
      )
      await self._save_properties(block_id, properties, scopes.get(block_id, {}))

    await self.uow.relations.delete_many(
      tuple(
        relation_id for pair, relation_id in self.members.items() if pair not in memberships
      )
    )
    for from_id, to_id in memberships - self.members.keys():
      await self.uow.relations.create(from_id, to_id, MEMBER)
    # DAV DELETE withdraws publication; independent graph references keep their meaning.
    for block_id, content in self.collection_content.items():
      if block_id not in active_collections and content.published:
        await self._save_block(
          block_id,
          COLLECTION,
          content.model_copy(update={"published": False}).model_dump_json(),
        )

  async def _save_properties(self, root_id, properties, uids):
    old = list(self.properties.get(root_id, []))
    for field in properties:
      link = field.link
      match = next(
        (
          p
          for p in old
          if p.field.link.component == link.component
          and p.field.link.name == link.name
          and p.field.value == field.value
          and p.field.link.representation == link.representation
        ),
        None,
      )
      target_id = match.relation.to_ if match else None
      if link.representation == "reference":
        targets = uids.get(field.value, [])
        if len(targets) == 1:
          target_id = targets[0]
      if target_id is None:
        target_id = await self._create_value(field)
      if match is not None:
        old.remove(match)
        if match.relation.content != link.to_relation() or match.relation.to_ != target_id:
          assert match.relation.id is not None
          await self.uow.relations.update(
            match.relation.id, to_=target_id, content=link.to_relation()
          )
      else:
        await self.uow.relations.create(root_id, target_id, link.to_relation())
    await self.uow.relations.delete_many(
      tuple(p.relation.id for p in old if p.relation.id is not None)
    )

  async def _create_value(self, field: PropertyValue) -> int:
    if isinstance(field.value, str):
      form = BlockForm(resolver="core.text.v1", content=field.value)
    else:
      storage = StorageManager.from_record(await self.uow.storage.get(-4))
      if not isinstance(storage, PostgreSQLBinaryStorage):
        raise ValueError("Database binary storage is unavailable")
      pointer = await storage.create_content(field.value, self.uow.storage)
      form = BlockForm(
        resolver=(
          ResolverManager.match_media_type(field.link.media_type)
          or ResolverManager.match_media_type(detect_media_type(field.value))
          or "core.file.v1"
        ),
        content=pointer,
        storage=-4,
      )
    block = await self.uow.blocks.create(form)
    assert block.id is not None
    return block.id

  async def _save_block(self, block_id: int | None, resolver: str, content: str) -> int:
    if block_id is None:
      block = await self.uow.blocks.create(BlockForm(resolver=resolver, content=content))
      assert block.id is not None
      self.original[block.id] = (resolver, content)
      return block.id
    if self.original[block_id] != (resolver, content):
      await self.uow.blocks.edit_block(block_id, content=content, resolver=resolver)
      self.original[block_id] = (resolver, content)
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


async def migrate_legacy_graph() -> None:
  """Atomically promote existing resources, keeping root IDs and external relations."""
  async with dav_transaction("w"):
    pass
