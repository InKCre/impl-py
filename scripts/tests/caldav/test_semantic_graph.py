"""Cross-peer reuse, graph-only authority and legacy transition at the DAV boundary."""

import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import anyio
import pytest
import sqlalchemy
import sqlmodel
import vobject  # pyrefly: ignore[untyped-import]
from sqlalchemy.exc import OperationalError

from app.business.info_base.resolver import ResolverManager
from app.business.info_base.storage import StorageManager
from app.business.info_base.storage.postgresql import (
  PostgreSQLBinaryStorage,
  PostgreSQLBlobPointer,
)
from app.persistence.info_base.uow import graph_uow
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.info_base.storage import StorageBlobModel
from extensions.caldav.graph import DAVGraph, migrate_legacy_graph
from extensions.caldav.resolver import solve_resource
from extensions.caldav.schema import (
  CONTACT,
  LEGACY_CONTACT,
  LEGACY_COLLECTION,
  GraphContent,
  ResourceContent,
)
from tests.database import TestSession
from scripts.tests.caldav.test_dav_graph import dav, pytestmark, create_calendar  # noqa: F401

PHOTO = base64.b64decode(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNQXpAHAAIbATIqos84AAAAAElFTkSuQmCC"
)


def card(uid, phones=(("CELL", "+8613800000000"), ("WORK", "+8613900000000")), photo=PHOTO):
  lines = [
    "BEGIN:VCARD",
    "VERSION:3.0",
    f"UID:{uid}",
    "FN:Shared contact",
    "N:Contact;Shared;;;",
    "NOTE:Shared note",
    "X-UNKNOWN;X-PARAM=kept:retained",
  ]
  lines.extend(f"TEL;TYPE={label}:{phone}" for label, phone in phones)
  lines.append("PHOTO;ENCODING=b;TYPE=PNG:" + base64.b64encode(photo).decode())
  return "\r\n".join([*lines, "END:VCARD", ""])


async def resource_graph(path):
  async with graph_uow(repeatable_read=True) as uow:
    graph = DAVGraph(uow)
    await graph.load()
    collection_path, href = path.removeprefix("/caldav/").rsplit("/", 1)
    resource = graph.collections[collection_path].items[href]
    assert resource.block_id is not None
    root = await uow.blocks.get(resource.block_id)
    properties = graph.properties.get(resource.block_id, [])
    blocks = await uow.blocks.get_many(tuple(p.relation.to_ for p in properties))
    return root, properties, {b.id: b for b in blocks}


def test_reusable_properties_reorder_shared_edits_and_publication_delete(dav):
  uid = uuid4().hex
  path = f"/caldav/inkcre/contacts/{uid}.vcf"
  assert dav.put(path, content=card(uid), headers={"If-None-Match": "*"}).status_code == 201
  root, fields, blocks = dav.portal.call(resource_graph, path)
  assert root.resolver == CONTACT
  remainder = GraphContent.model_validate_json(root.content).remainder
  assert "TEL" not in remainder and "PHOTO" not in remainder and "NOTE" not in remainder
  phones = {p.field.value: p.relation.to_ for p in fields if p.field.link.name == "TEL"}
  photo = next(blocks[p.relation.to_] for p in fields if p.field.link.name == "PHOTO")
  assert photo.resolver == "core.image.v1" and photo.storage == -4
  solved_photo = dav.portal.call(ResolverManager.get(photo).get_solved_content)
  assert solved_photo.width == 1 and solved_photo.height == 1
  assert all(blocks[block_id].resolver == "core.text.v1" for block_id in phones.values())

  async def cite():
    async with graph_uow() as uow:
      note = await uow.blocks.create(
        BlockForm(resolver="core.text.v1", content="This contact photo is evidence.")
      )
      assert note.id is not None
      relation = await uow.relations.create(note.id, photo.id, "evidence")
      root_reference = await uow.relations.create(note.id, root.id, "contact")
      return relation.id, root_reference.id

  citation, root_citation = dav.portal.call(cite)
  before = dav.get(path)
  reordered = card(uid, phones=(("HOME", "+8613900000000"), ("CELL", "+8613800000000")))
  assert (
    dav.put(
      path, content=reordered, headers={"If-Match": before.headers["etag"]}
    ).status_code
    == 204
  )
  _, fields, _ = dav.portal.call(resource_graph, path)
  assert phones == {
    p.field.value: p.relation.to_ for p in fields if p.field.link.name == "TEL"
  }

  async def peer_update():
    async with graph_uow() as uow:
      await uow.blocks.edit_block(phones["+8613800000000"], content="+8613800000001")
      storage = StorageManager.from_record(await uow.storage.get(-4))
      assert isinstance(storage, PostgreSQLBinaryStorage)
      await storage.update_content(photo.content, PHOTO + b"updated", uow.storage)

  before = dav.get(path)
  dav.portal.call(peer_update)
  updated = dav.get(path)
  assert (
    "+8613800000001" in updated.text and updated.headers["etag"] != before.headers["etag"]
  )
  assert vobject.readOne(updated.text).photo.value == PHOTO + b"updated"
  assert (
    dav.put(
      path, content=reordered, headers={"If-Match": before.headers["etag"]}
    ).status_code
    == 412
  )

  # Source changes rebind values; they must not rewrite media another graph already cites.
  changed = card(uid, photo=PHOTO + b"new-source-photo")
  assert (
    dav.put(
      path, content=changed, headers={"If-Match": updated.headers["etag"]}
    ).status_code
    == 204
  )
  _, next_fields, _ = dav.portal.call(resource_graph, path)
  next_photo = next(p.relation.to_ for p in next_fields if p.field.link.name == "PHOTO")
  assert next_photo != photo.id
  assert dav.delete(path, headers={"If-Match": dav.get(path).headers["etag"]}).is_success
  assert dav.get(path).status_code == 404

  async def retained():
    async with graph_uow() as uow:
      assert await uow.relations.get_by_id(citation) is not None
      assert await uow.relations.get_by_id(root_citation) is not None
      original_photo = await uow.blocks.get(photo.id)
      assert original_photo is not None
      storage = StorageManager.from_record(await uow.storage.get(-4))
      assert isinstance(storage, PostgreSQLBinaryStorage)
      assert (
        await storage.read_content(original_photo.content, uow.storage)
        == PHOTO + b"updated"
      )
      assert await uow.blocks.get(root.id) is not None
    assert (
      vobject.readOne((await solve_resource(root.id)).text).photo.value
      == PHOTO + b"new-source-photo"
    )

  dav.portal.call(retained)


def task(uid, parent=None):
  return "\r\n".join(
    [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//InKCre//test//EN",
      "BEGIN:VTODO",
      f"UID:{uid}",
      "DTSTAMP:20261021T000000Z",
      "SUMMARY:Graph task",
      *([f"RELATED-TO;RELTYPE=PARENT:{parent}"] if parent else []),
      "END:VTODO",
      "END:VCALENDAR",
      "",
    ]
  )


def test_child_before_parent_becomes_a_real_relation_and_survives_unpublish(dav):
  path = create_calendar(dav)
  child_uid, parent_uid = uuid4().hex, uuid4().hex
  child_path, parent_path = path + "child.ics", path + "parent.ics"
  try:
    assert (
      dav.put(
        child_path, content=task(child_uid, parent_uid), headers={"If-None-Match": "*"}
      ).status_code
      == 201
    )
    child, fields, blocks = dav.portal.call(resource_graph, child_path)
    reference = next(p for p in fields if p.field.link.name == "RELATED-TO")
    assert blocks[reference.relation.to_].resolver == "core.text.v1"
    assert (
      dav.put(
        parent_path, content=task(parent_uid), headers={"If-None-Match": "*"}
      ).status_code
      == 201
    )
    parent, _, _ = dav.portal.call(resource_graph, parent_path)
    child, fields, _ = dav.portal.call(resource_graph, child_path)
    assert (
      next(p.relation.to_ for p in fields if p.field.link.name == "RELATED-TO") == parent.id
    )
    assert "RELATED-TO" not in GraphContent.model_validate_json(child.content).remainder
    assert dav.delete(parent_path).is_success
    assert parent_uid in dav.get(child_path).text
  finally:
    dav.delete(path)


@pytest.mark.parametrize("target", ["phone", "blob"])
def test_conditional_write_locks_the_actual_semantic_dependency(dav, monkeypatch, target):
  uid = uuid4().hex
  path = f"/caldav/inkcre/contacts/{uid}.vcf"
  assert dav.put(path, content=card(uid), headers={"If-None-Match": "*"}).status_code == 201
  _, fields, blocks = dav.portal.call(resource_graph, path)
  phone = next(p.relation.to_ for p in fields if p.field.link.name == "TEL")
  photo = next(blocks[p.relation.to_] for p in fields if p.field.link.name == "PHOTO")
  etag = dav.get(path).headers["etag"]
  loaded, resume = Event(), Event()
  original_load = DAVGraph.load

  async def pause(self, *, lock=False):
    await original_load(self, lock=lock)
    if lock:
      loaded.set()
      await anyio.to_thread.run_sync(resume.wait)

  monkeypatch.setattr(DAVGraph, "load", pause)
  try:
    with ThreadPoolExecutor(max_workers=1) as pool:
      write = pool.submit(dav.put, path, content=card(uid), headers={"If-Match": etag})
      assert loaded.wait(10)
      try:
        with TestSession() as session:
          session.connection().execute(sqlalchemy.text("SET LOCAL lock_timeout = '150ms'"))
          with pytest.raises(OperationalError) as error:
            if target == "phone":
              session.connection().execute(
                sqlalchemy.update(BlockModel)
                .where(BlockModel.id == phone)
                .values(content="concurrent")
              )
            else:
              pointer = PostgreSQLBlobPointer.model_validate_json(photo.content)
              session.connection().execute(
                sqlalchemy.update(StorageBlobModel)
                .where(sqlmodel.col(StorageBlobModel.id) == pointer.blob_id)
                .values(data=b"concurrent")
              )
          assert getattr(error.value.orig, "sqlstate", None) == "55P03"
          session.rollback()
      finally:
        resume.set()
      assert write.result(10).status_code == 204
  finally:
    resume.set()
    monkeypatch.setattr(DAVGraph, "load", original_load)
    dav.delete(path)


def test_legacy_promotion_is_atomic_keeps_root_id_and_unknown_fields(dav, monkeypatch):
  uid, collection_path = uuid4().hex, "inkcre/legacy-" + uuid4().hex

  async def seed():
    async with graph_uow() as uow:
      collection = await uow.blocks.create(
        BlockForm(
          resolver=LEGACY_COLLECTION,
          content='{"path":"' + collection_path + '","props":{"tag":"VADDRESSBOOK"}}',
        )
      )
      root = await uow.blocks.create(
        BlockForm(
          resolver=LEGACY_CONTACT,
          content=ResourceContent(href="legacy.vcf", text=card(uid)).model_dump_json(),
        )
      )
      assert collection.id is not None and root.id is not None
      await uow.relations.create(collection.id, root.id, "member")
      ref = await uow.blocks.create(
        BlockForm(resolver="core.text.v1", content="Legacy reference")
      )
      assert ref.id is not None
      relation = await uow.relations.create(ref.id, root.id, "contact")
      return root.id, relation.id

  root_id, relation_id = dav.portal.call(seed)
  original = DAVGraph._create_value

  async def fail(self, field):
    await original(self, field)
    raise RuntimeError("injected migration failure")

  monkeypatch.setattr(DAVGraph, "_create_value", fail)
  with pytest.raises(RuntimeError, match="injected"):
    dav.portal.call(migrate_legacy_graph)

  async def read():
    async with graph_uow() as uow:
      root = await uow.blocks.get(root_id)
      assert await uow.relations.get_by_id(relation_id) is not None
      return root

  assert dav.portal.call(read).resolver == LEGACY_CONTACT
  monkeypatch.setattr(DAVGraph, "_create_value", original)
  dav.portal.call(migrate_legacy_graph)
  root = dav.portal.call(read)
  assert root.resolver == CONTACT
  result = dav.get("/caldav/" + collection_path + "/legacy.vcf")
  assert result.status_code == 200 and "X-UNKNOWN;X-PARAM=kept:retained" in result.text
  assert vobject.readOne(result.text).photo.value == PHOTO
  dav.delete("/caldav/" + collection_path + "/")
