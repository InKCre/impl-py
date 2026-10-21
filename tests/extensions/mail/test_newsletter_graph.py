"""Real persistence checks for publication grouping and lossless body upgrades."""

import datetime
import os

import pytest

from app.business.info_base.resolver import ResolverManager
from app.business.source import SourceManager
from app.schemas.info_base.block import BlockForm
from app.persistence.source.uow import source_uow
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
from tests.extensions.mail.acceptance.test_mail_vertical import (
  MAIL_SOURCE_TYPE,
  _bootstrap_runtime_catalogs,
  _reset_acceptance_database,
)
from tests.extensions.mail.test_newsletter import HTML


pytestmark = [
  pytest.mark.integration,
  pytest.mark.skipif(
    "mail_acceptance" not in os.getenv("INKCRE_TEST_DATABASE_URL", ""),
    reason="requires a disposable mail_acceptance database",
  ),
]


def test_publication_grouping_replay_and_historical_body_upgrade(async_runner):
  _reset_acceptance_database()

  async def journey():
    await _bootstrap_runtime_catalogs()
    source = await SourceManager.create(
      MAIL_SOURCE_TYPE,
      config={
        "parameters": {"host": "mail.invalid", "username": "reader", "password": "test"}
      },
    )
    publication = CanonicalMailingList(list_id="weekly.example.org", name="Research Weekly")
    message = MessageFact(
      uid=1,
      uid_validity=1,
      root=CanonicalEmail(
        message_id="issue-1@example.org",
        subject="Weekly research",
        authored_at=datetime.datetime(2026, 10, 20, tzinfo=datetime.timezone.utc),
      ),
      mailing_list=publication,
      bodies=(BodyFact(part_id="1", media_type="text/html", content=HTML),),
    )
    async with source_uow() as uow:
      reconciler = await MailGraphReconciler.for_source(uow, source)
      mailbox = await reconciler.ensure_mailbox(
        MailboxFact(mailbox=CanonicalMailbox(name="INBOX"), uid_validity=1)
      )
      first = await reconciler.reconcile_message(mailbox, message)
      again = await reconciler.reconcile_message(mailbox, message)
      assert first.id == again.id
      second = await reconciler.reconcile_message(
        mailbox,
        message.model_copy(
          update={
            "uid": 2,
            "root": message.root.model_copy(update={"message_id": "issue-2@example.org"}),
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
          "root": message.root.model_copy(update={"message_id": "legacy@example.org"}),
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
            update={"message_id": "older@example.org"}
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

    solved = await ResolverManager.get(first).get_solved_content(materialize_missing=False)
    assert solved.mailing_lists[0].solved_content.list_id == publication.list_id
    assert "Preview noise" not in solved.bodies[0].solved_content
    archive = await ResolverManager.get(publications[0]).get_solved_content(
      materialize_missing=False
    )
    assert len(archive.emails) == 5

  async_runner.run(journey())
