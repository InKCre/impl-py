"""HTTP/graph integration protects conditional writes and native DAV round trips."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.engine import ASYNC_DB_ENGINE
from app.persistence.info_base.uow import graph_uow
from app.business.info_base.resolver import ResolverManager
from extensions.caldav import Extension
from extensions.caldav.schema import CALENDAR, ResourceContent
from tests.extensions.runtime_support import publish_extension

pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)

PASSWORD = "caldav-integration-test-" + uuid4().hex


@pytest.fixture
def dav():
  published = publish_extension(
    Extension, {"password": PASSWORD}, persist_config=True, raise_server_exceptions=False
  )
  with published.client as client:
    client.auth = ("inkcre", PASSWORD)
    try:
      yield client
    finally:
      assert client.portal is not None
      client.portal.call(ASYNC_DB_ENGINE.dispose)
      published.unpublish()


def calendar(summary="InKCre 测试"):
  return "\r\n".join(
    [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//InKCre//DAV integration//EN",
      "BEGIN:VEVENT",
      "UID:inkcre-roundtrip",
      "DTSTAMP:20261020T000000Z",
      "DTSTART:20261020T010000Z",
      "DTEND:20261020T020000Z",
      "RRULE:FREQ=DAILY;COUNT=3",
      f"SUMMARY:{summary}",
      "X-INKCRE-TEST:preserved",
      "BEGIN:VALARM",
      "ACTION:DISPLAY",
      "TRIGGER:-PT15M",
      "DESCRIPTION:提醒",
      "END:VALARM",
      "END:VEVENT",
      "BEGIN:VEVENT",
      "UID:inkcre-roundtrip",
      "DTSTAMP:20261020T000000Z",
      "RECURRENCE-ID:20261021T010000Z",
      "DTSTART:20261021T030000Z",
      "DTEND:20261021T040000Z",
      "SUMMARY:单次例外",
      "END:VEVENT",
      "END:VCALENDAR",
      "",
    ]
  )


def create_calendar(client):
  path = f"/caldav/inkcre/test-{uuid4().hex}/"
  response = client.request("MKCALENDAR", path)
  assert response.status_code == 201, response.text
  return path


def test_native_roundtrip_graph_conflicts_and_sync_recovery(dav):
  response = dav.request("PROPFIND", "/caldav/", headers={"Depth": "1"})
  assert response.status_code == 207, response.text
  path = create_calendar(dav)
  resource = path + "event.ics"
  try:
    response = dav.put(
      resource, content=calendar().encode(), headers={"If-None-Match": "*"}
    )
    assert response.status_code == 201, response.text
    old_etag = response.headers["etag"]
    stored = dav.get(resource)
    assert stored.status_code == 200
    for fact in (
      "RRULE:FREQ=DAILY;COUNT=3",
      "RECURRENCE-ID:",
      "BEGIN:VALARM",
      "X-INKCRE-TEST:preserved",
      "单次例外",
    ):
      assert fact in stored.text

    async def read_info_base():
      async with graph_uow() as uow:
        roots = await uow.blocks.get_by_resolvers((CALENDAR,))
        root = next(
          b
          for b in roots
          if ResourceContent.model_validate_json(b.content).href == "event.ics"
        )
        resolver = ResolverManager.get(root)
        return await resolver.get_label(), await resolver.get_text()

    assert dav.portal is not None
    label, text = dav.portal.call(read_info_base)
    assert "InKCre 测试" in label and "X-INKCRE-TEST:preserved" in text

    barrier = Barrier(2)

    def update(summary):
      barrier.wait()
      return dav.put(
        resource, content=calendar(summary).encode(), headers={"If-Match": old_etag}
      ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
      statuses = list(pool.map(update, ("并发甲", "并发乙")))
    assert sorted(statuses) == [204, 412]
    assert dav.put(resource, content=calendar().encode()).status_code == 412

    report = (
      '<D:sync-collection xmlns:D="DAV:"><D:sync-token/>'
      "<D:sync-level>1</D:sync-level><D:prop><D:getetag/></D:prop>"
      "</D:sync-collection>"
    )
    initial = dav.request("REPORT", path, content=report)
    assert initial.status_code == 207
    token = ET.fromstring(initial.content).findtext("{DAV:}sync-token")
    assert token
    same_report = report.replace("<D:sync-token/>", f"<D:sync-token>{token}</D:sync-token>")
    same = dav.request("REPORT", path, content=same_report)
    assert not ET.fromstring(same.content).findall("{DAV:}response")
    assert dav.delete(
      resource, headers={"If-Match": dav.get(resource).headers["etag"]}
    ).is_success
    expired = dav.request("REPORT", path, content=same_report)
    assert expired.status_code == 403 and "valid-sync-token" in expired.text
    rescan = dav.request("PROPFIND", path, headers={"Depth": "1"})
    assert "event.ics" not in rescan.text
    assert dav.get(resource).status_code == 404
  finally:
    dav.delete(path)


def test_commit_failure_rolls_back_and_never_reports_success(dav):
  path = create_calendar(dav)
  resource = path + "rollback.ics"
  try:
    assert (
      dav.put(
        resource, content=calendar().encode(), headers={"If-None-Match": "*"}
      ).status_code
      == 201
    )
    original = dav.get(resource)

    def fail_commit(session):
      # Only fail the mutation transaction, after it has flushed changed content.
      if session.info.get("caldav_test_mutated"):
        raise RuntimeError("injected commit failure")

    def record_write(session, flush_context):
      session.info["caldav_test_mutated"] = True

    event.listen(Session, "after_flush", record_write)
    event.listen(Session, "before_commit", fail_commit)
    try:
      failed = dav.put(
        resource,
        content=calendar("不应保存").encode(),
        headers={"If-Match": original.headers["etag"]},
      )
      assert failed.status_code == 500
    finally:
      event.remove(Session, "after_flush", record_write)
      event.remove(Session, "before_commit", fail_commit)
    restored = dav.get(resource)
    assert restored.text == original.text
    assert restored.headers["etag"] == original.headers["etag"]
  finally:
    dav.delete(path)


def test_tasks_contacts_and_credential_revocation(dav):
  from app.schemas.extension import ExtensionModel
  from tests.database import TestSession

  task_path = f"/caldav/inkcre/tasks/{uuid4().hex}.ics"
  contact_path = f"/caldav/inkcre/contacts/{uuid4().hex}.vcf"
  task = "\r\n".join(
    [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//InKCre//test//EN",
      "BEGIN:VTODO",
      f"UID:{uuid4()}",
      "DTSTAMP:20261020T000000Z",
      "SUMMARY:测试子任务",
      "DUE:20261021T080000Z",
      "RELATED-TO;RELTYPE=PARENT:test-parent",
      "STATUS:COMPLETED",
      "PERCENT-COMPLETE:100",
      "COMPLETED:20261020T090000Z",
      "X-APPLE-SORT-ORDER:42",
      "END:VTODO",
      "END:VCALENDAR",
      "",
    ]
  )
  contact = "\r\n".join(
    [
      "BEGIN:VCARD",
      "VERSION:3.0",
      f"UID:{uuid4()}",
      "FN:InKCre 测试联系人",
      "N:测试;联系人;;;",
      "TEL;TYPE=CELL:+8613800000000",
      "TEL;TYPE=WORK:+8613900000000",
      "EMAIL:caldav-test@example.invalid",
      "ADR;TYPE=HOME:;;测试地址;上海;;;中国",
      "NOTE:保留备注",
      "PHOTO;ENCODING=b;TYPE=PNG:aGVsbG8=",
      "X-INKCRE-TEST:kept",
      "END:VCARD",
      "",
    ]
  )
  try:
    for path, text in ((task_path, task), (contact_path, contact)):
      response = dav.put(path, content=text.encode(), headers={"If-None-Match": "*"})
      assert response.status_code == 201, response.text
    saved_task = dav.get(task_path).text
    assert (
      "STATUS:COMPLETED" in saved_task
      and "RELATED-TO;RELTYPE=PARENT:test-parent" in saved_task
    )
    assert "X-APPLE-SORT-ORDER:42" in saved_task
    saved_contact = dav.get(contact_path).text
    for value in ("+8613800000000", "+8613900000000", "保留备注", "PHOTO", "X-INKCRE-TEST"):
      assert value in saved_contact
    with TestSession() as session:
      installed = session.get(ExtensionModel, "inkcre/caldav")
      assert installed
      installed.config = {"password": None}
      session.add(installed)
      session.commit()
    assert dav.get(task_path).status_code == 401
  finally:
    with TestSession() as session:
      installed = session.get(ExtensionModel, "inkcre/caldav")
      assert installed
      installed.config = {"password": PASSWORD}
      session.add(installed)
      session.commit()
    dav.delete(task_path)
    dav.delete(contact_path)
  Extension.unpublish()
  assert dav.get("/caldav/").status_code == 404


def test_move_rejects_stale_source_and_retains_graph_identity(dav):
  path = create_calendar(dav)
  source, target = path + "source.ics", path + "target.ics"
  try:
    created = dav.put(source, content=calendar().encode(), headers={"If-None-Match": "*"})
    assert created.status_code == 201
    changed = dav.put(
      source,
      content=calendar("新版本").encode(),
      headers={"If-Match": created.headers["etag"]},
    )
    assert changed.status_code == 204
    headers = {
      "Destination": "http://testserver" + target,
      "If-Match": created.headers["etag"],
    }
    assert dav.request("MOVE", source, headers=headers).status_code == 412
    headers["If-Match"] = changed.headers["etag"]
    assert dav.request("MOVE", source, headers=headers).status_code == 201
    assert dav.get(source).status_code == 404
    assert "新版本" in dav.get(target).text
  finally:
    dav.delete(path)


@pytest.mark.parametrize("change", ["move", "insert"])
def test_collection_delete_serializes_direct_peer_membership_writes(
  dav, monkeypatch, change
):
  """Direct graph writes cannot move/add a member under an in-flight DAV deletion."""
  from threading import Event
  import anyio
  import sqlalchemy
  import sqlmodel
  from sqlalchemy.exc import OperationalError

  from app.schemas.info_base.block import BlockModel
  from app.schemas.info_base.relation import RelationModel
  from extensions.caldav.graph import DAVGraph
  from extensions.caldav.schema import COLLECTION, CollectionContent
  from tests.database import TestSession

  path, other = create_calendar(dav), create_calendar(dav)
  resource = path + "member.ics"
  assert (
    dav.put(
      resource, content=calendar().encode(), headers={"If-None-Match": "*"}
    ).status_code
    == 201
  )
  loaded, resume = Event(), Event()
  original_load = DAVGraph.load

  async def pause_delete(self, *, lock=False):
    await original_load(self, lock=lock)
    if lock:
      loaded.set()
      await anyio.to_thread.run_sync(resume.wait)

  monkeypatch.setattr(DAVGraph, "load", pause_delete)
  try:
    with ThreadPoolExecutor(max_workers=1) as pool:
      deletion = pool.submit(dav.delete, path)
      assert loaded.wait(10)
      try:
        with TestSession() as session:
          collections = {
            CollectionContent.model_validate_json(b.content).path: b.id
            for b in session.exec(
              sqlmodel.select(BlockModel).where(BlockModel.resolver == COLLECTION)
            ).all()
          }
          source_id = collections[path.removeprefix("/caldav/").strip("/")]
          target_id = collections[other.removeprefix("/caldav/").strip("/")]
          assert source_id is not None and target_id is not None
          member = session.exec(
            sqlmodel.select(RelationModel).where(
              RelationModel.from_ == source_id, RelationModel.content == "member"
            )
          ).one()
          session.execute(sqlalchemy.text("SET LOCAL lock_timeout = '150ms'"))
          with pytest.raises(OperationalError) as failed:
            if change == "move":
              member.from_ = target_id
              session.add(member)
            else:
              item = BlockModel(
                resolver=CALENDAR,
                content=ResourceContent(
                  href="new.ics", text=calendar("并发新增")
                ).model_dump_json(),
              )
              session.add(item)
              session.flush()
              session.add(RelationModel(from_=source_id, to_=item.id, content="member"))
            session.flush()
          assert getattr(failed.value.orig, "sqlstate", None) == "55P03"
          session.rollback()
      finally:
        resume.set()
      assert deletion.result(10).is_success
    assert (
      dav.request("PROPFIND", "/caldav/inkcre/", headers={"Depth": "1"}).status_code == 207
    )
  finally:
    resume.set()
    monkeypatch.setattr(DAVGraph, "load", original_load)
    dav.delete(path)
    dav.delete(other)
