"""Real persistence checks for publication grouping and lossless body upgrades."""

import datetime
import os
from uuid import uuid4

import pytest

from inkcre_core.business.info_base.resolver import ResolverManager
from inkcre_core.business.source import SourceManager
from inkcre_core.schemas.info_base.block import BlockForm
from inkcre_core.persistence.source.uow import source_uow
from extensions.mail.newsletter import MAILING_LIST_RESOLVER, NEWSLETTER_HTML_RESOLVER
from extensions.mail.reconcile import MailGraphReconciler
from extensions.mail.schema import (
  BodyFact,
  CanonicalEmail,
  CanonicalMailbox,
  CanonicalMailingList,
  MailboxFact,
  MessageFact,
)
from extensions.mail import Extension
from extensions.mail.source import Source
from tests.extensions.mail.test_newsletter import HTML


pytestmark = [
  pytest.mark.integration,
  pytest.mark.skipif(
    not os.getenv("INKCRE_TEST_DATABASE_URL"),
    reason="requires an explicitly selected migrated disposable PostgreSQL runtime",
  ),
]


@pytest.fixture
def newsletter_source(async_runner):
  owned_blocks = set()

  async def create():
    Extension.load_decoders()
    await SourceManager.sync_source_types_async()
    return await SourceManager.create(
      f"{Source.__module__}.{Source.__qualname__}",
      config={
        "parameters": {"host": "mail.invalid", "username": "reader", "password": "test"}
      },
    )

  source = async_runner.run(create())
  try:
    yield source, owned_blocks
  finally:

    async def cleanup():
      await SourceManager.delete(source.id)
      async with source_uow() as uow:
        for block_id in owned_blocks:
          await uow.graph.blocks.delete(block_id)

    async_runner.run(cleanup())


def test_publication_grouping_replay_and_historical_body_upgrade(
  async_runner, newsletter_source
):
  source, owned_blocks = newsletter_source
  identity = uuid4().hex

  async def journey():
    publication = CanonicalMailingList(
      list_id=f"{identity}.example.org", name="Research Weekly"
    )
    message = MessageFact(
      uid=1,
      uid_validity=1,
      root=CanonicalEmail(
        message_id=f"issue-1-{identity}@example.org",
        subject="Weekly research",
        authored_at=datetime.datetime(2026, 10, 20, tzinfo=datetime.timezone.utc),
      ),
      mailing_list=publication,
      bodies=(BodyFact(part_id="1", media_type="text/html", content=HTML),),
    )
    async with source_uow() as uow:
      reconciler = await MailGraphReconciler.for_source(uow, source)
      owned_blocks.add(reconciler.source_block.id)
      mailbox = await reconciler.ensure_mailbox(
        MailboxFact(mailbox=CanonicalMailbox(name="INBOX"), uid_validity=1)
      )
      assert mailbox.id is not None
      first = await reconciler.reconcile_message(mailbox, message)
      second = await reconciler.reconcile_message(
        mailbox,
        message.model_copy(
          update={
            "uid": 2,
            "root": message.root.model_copy(
              update={"message_id": f"issue-2-{identity}@example.org"}
            ),
          }
        ),
      )
      assert second.id != first.id
      duplicate = await reconciler.reconcile_message(
        mailbox, message.model_copy(update={"uid": 3})
      )
      assert duplicate.id != first.id  # Keep exact remote occurrence provenance.
      legacy_fact = message.model_copy(
        update={
          "uid": 4,
          "mailing_list": None,
          "root": message.root.model_copy(
            update={"message_id": f"legacy-{identity}@example.org"}
          ),
        }
      )
      legacy = await reconciler.reconcile_message(mailbox, legacy_fact)
      assert legacy.id is not None
      relations = await uow.graph.relations.get(legacy.id, include_in=False)
      old_body_id = next(item.to_ for item in relations if '"role":"body"' in item.content)
      old_body = await uow.graph.blocks.get(old_body_id)
      assert old_body is not None
      assert old_body.resolver == "core.html.v1"
      await reconciler.reconcile_message(
        mailbox, legacy_fact.model_copy(update={"mailing_list": publication})
      )
      upgraded = await uow.graph.blocks.get(old_body_id)
      assert upgraded is not None
      assert upgraded.resolver == NEWSLETTER_HTML_RESOLVER
      assert upgraded.content == HTML
      publications = await uow.graph.blocks.find_json_field(
        MAILING_LIST_RESOLVER, "list_id", publication.list_id
      )
      assert len(publications) == 1
      assert publications[0].id is not None
      published = await uow.graph.relations.get(
        publications[0].id, include_in=False, content="publishes"
      )
      assert {item.to_ for item in published} == {
        first.id,
        second.id,
        duplicate.id,
        legacy.id,
      }
      assert len(published) == 4
      duplicate_publication = await uow.graph.blocks.create(
        BlockForm(resolver=MAILING_LIST_RESOLVER, content=publication.model_dump_json())
      )
      historical_issue = await uow.graph.blocks.create(
        BlockForm(
          resolver="extensions.mail.email.v1",
          content=message.root.model_copy(
            update={"message_id": f"older-{identity}@example.org"}
          ).model_dump_json(),
        )
      )
      assert duplicate_publication.id is not None and historical_issue.id is not None
      await uow.graph.relations.create(
        duplicate_publication.id, historical_issue.id, "publishes"
      )
      await reconciler.reconcile_message(mailbox, message)
      assert (
        len(
          await uow.graph.blocks.find_json_field(
            MAILING_LIST_RESOLVER, "list_id", publication.list_id
          )
        )
        == 2
      )
      roots = (mailbox, first, second, duplicate, legacy, historical_issue)
      owned_blocks.update(block.id for block in (*roots, *publications))
      owned_blocks.add(duplicate_publication.id)
      for root in (first, second, duplicate, legacy):
        assert root.id is not None
        owned_blocks.update(
          relation.to_
          for relation in await uow.graph.relations.get(root.id, include_in=False)
        )

    # Replay after commit with a fresh reconciler, not an in-transaction object copy.
    async with source_uow() as uow:
      reconciler = await MailGraphReconciler.for_source(uow, source)
      saved_mailbox = await uow.graph.blocks.get(mailbox.id)
      assert saved_mailbox is not None
      replayed = await reconciler.reconcile_message(saved_mailbox, message)
      assert replayed.id == first.id

    solved = await ResolverManager.get(first).get_solved_content(materialize_missing=False)
    assert solved.mailing_lists[0].solved_content.list_id == publication.list_id
    assert "Preview noise" not in solved.bodies[0].solved_content
    archive = await ResolverManager.get(publications[0]).get_solved_content(
      materialize_missing=False
    )
    assert len(archive.emails) == 5

  async_runner.run(journey())
