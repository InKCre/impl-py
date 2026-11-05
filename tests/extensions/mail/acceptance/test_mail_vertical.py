"""J1–J3: real IMAP -> Job -> graph -> Resolver/Storage acceptance."""

from __future__ import annotations
from inkcre_core import current_core

import collections.abc
import datetime
import json
import os
from pathlib import Path

import pytest
import sqlalchemy
import sqlmodel

from inkcre_core.business.info_base.resolver import ResolverManager, register_core_resolvers
from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.job import JobManager
from inkcre_core.business.lexical_retrieval import LexicalRetrievalManager
from inkcre_core.business.source import (
  SOURCE_BACKFILL_JOB_TYPE,
  SOURCE_COLLECT_JOB_TYPE,
  SourceManager,
)
from tests.database import TestSession
from inkcre_core.schemas.extension import ExtensionModel
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.info_base.storage import StorageBlobModel
from inkcre_core.schemas.job import JobModel, JobStatus
from inkcre_core.schemas.lexical_retrieval import LexicalMaintenanceOptions
from inkcre_core.schemas.source import SourceModel
from extensions.mail import Extension
from extensions.mail.reconcile import (
  EMAIL_ADDRESS_RESOLVER,
  EMAIL_RESOLVER,
  MAILBOX_RESOLVER,
  MAIL_FLAG_RESOLVER,
  MIME_PART_RESOLVER,
)
from extensions.mail.resolver import MailMaterializationUnavailable
from extensions.mail.schema import (
  CanonicalEmail,
  CanonicalMailbox,
  CanonicalMailFlag,
  CanonicalMimePart,
  MailSourceConfig,
  MailSourceState,
  SolvedEmail,
  SolvedMimePart,
)
from extensions.mail.source import Source

from .imap_harness import DovecotHarness


pytestmark = [pytest.mark.acceptance, pytest.mark.integration]

CORPUS = Path(__file__).parent / "corpus"
MAIL_SOURCE_TYPE = f"{Source.__module__}.{Source.__qualname__}"
UTC = datetime.timezone.utc


@pytest.fixture(scope="module")
def dovecot() -> collections.abc.Iterator[DovecotHarness]:
  distribution = os.getenv("INKCRE_DOVECOT_ACCEPTANCE_ROOT")
  work_root = os.getenv("INKCRE_ACCEPTANCE_WORK_ROOT")
  if not distribution or not work_root:
    pytest.skip("requires INKCRE_DOVECOT_ACCEPTANCE_ROOT and INKCRE_ACCEPTANCE_WORK_ROOT")
  harness = DovecotHarness(Path(distribution), Path(work_root))
  harness.start()
  assert {"UIDPLUS", "CONDSTORE", "QRESYNC"} <= harness.capabilities()
  try:
    yield harness
  finally:
    harness.stop()


def _reset_acceptance_database() -> None:
  database_url = os.getenv("INKCRE_TEST_DATABASE_URL", "")
  if "mail_acceptance" not in database_url:
    raise RuntimeError("Mail acceptance requires an explicitly named disposable database")
  with TestSession() as db:
    db.connection().execute(
      sqlalchemy.text(
        "TRUNCATE TABLE "
        "inkcre.crons, inkcre.jobs, inkcre.relation_embeddings, "
        "inkcre.block_embeddings, inkcre.relations, inkcre.sources, "
        "inkcre.storage_blobs, inkcre.blocks RESTART IDENTITY CASCADE"
      )
    )
    extension = db.get(ExtensionModel, "inkcre/mail")
    if extension is None:
      extension = ExtensionModel(
        name="inkcre/mail",
        config={},
      )
    extension.config = {
      "default_excluded_mailboxes": {
        "names": ["Excluded"],
        "special_uses": [],
      }
    }
    db.add(extension)
    db.commit()
  current_core().sources.clear()


async def _bootstrap_runtime_catalogs() -> None:
  register_core_resolvers()
  Extension.load_decoders()
  await StorageManager.setup_builtin_storages_async()
  await SourceManager.sync_source_types_async()
  await JobManager.sync_job_types()


async def _run_job(
  job_type: str,
  source_id: int,
  config: dict | None = None,
) -> JobModel:
  job = await JobManager.create(
    job_type,
    {"source": source_id, "config": config or {}},
  )
  assert job.id is not None
  assert await JobManager.run(job.id)
  with TestSession() as db:
    persisted = db.get(JobModel, job.id)
    assert persisted is not None
    return persisted


def _email(message_id: str) -> BlockModel | None:
  with TestSession() as db:
    matches = [
      block
      for block in db.exec(
        sqlmodel.select(BlockModel).where(BlockModel.resolver == EMAIL_RESOLVER)
      ).all()
      if CanonicalEmail.model_validate_json(block.content).message_id == message_id
    ]
  assert len(matches) <= 1
  return matches[0] if matches else None


def _mailbox(name: str) -> BlockModel | None:
  with TestSession() as db:
    matches = [
      block
      for block in db.exec(
        sqlmodel.select(BlockModel).where(BlockModel.resolver == MAILBOX_RESOLVER)
      ).all()
      if CanonicalMailbox.model_validate_json(block.content).name == name
    ]
  assert len(matches) <= 1
  return matches[0] if matches else None


def _locator(mailbox_id: int, uid: int) -> RelationModel | None:
  with TestSession() as db:
    matches = []
    for relation in db.exec(
      sqlmodel.select(RelationModel).where(RelationModel.from_ == mailbox_id)
    ).all():
      try:
        content = json.loads(relation.content)
      except json.JSONDecodeError:
        continue
      if content.get("type") == "contains" and content.get("uid") == uid:
        matches.append(relation)
  assert len(matches) <= 1
  return matches[0] if matches else None


def _components(email_id: int) -> list[tuple[RelationModel, BlockModel, dict]]:
  with TestSession() as db:
    result = []
    for relation in db.exec(
      sqlmodel.select(RelationModel).where(RelationModel.from_ == email_id)
    ).all():
      try:
        content = json.loads(relation.content)
      except json.JSONDecodeError:
        continue
      if content.get("role") not in {"body", "attachment", "inline"}:
        continue
      block = db.get(BlockModel, relation.to_)
      assert block is not None
      result.append((relation, block, content))
    return result


def _flag_names(email_id: int) -> set[str]:
  with TestSession() as db:
    names = set()
    for relation in db.exec(
      sqlmodel.select(RelationModel).where(
        RelationModel.to_ == email_id,
        RelationModel.content == "tags",
      )
    ).all():
      block = db.get(BlockModel, relation.from_)
      if block is not None and block.resolver == MAIL_FLAG_RESOLVER:
        names.add(CanonicalMailFlag.model_validate_json(block.content).name)
    return names


def _graph_counts() -> tuple[int, int]:
  with TestSession() as db:
    return (
      len(db.exec(sqlmodel.select(BlockModel)).all()),
      len(db.exec(sqlmodel.select(RelationModel)).all()),
    )


def _source(source_id: int) -> SourceModel:
  with TestSession() as db:
    source = db.get(SourceModel, source_id)
    assert source is not None
    return source


def _set_source_config(source_id: int, config: MailSourceConfig) -> None:
  with TestSession() as db:
    source = db.get(SourceModel, source_id)
    assert source is not None
    source.config = config.model_dump(mode="json")
    db.add(source)
    db.commit()


def _set_extension_exclusion(name: str) -> None:
  with TestSession() as db:
    extension = db.get(ExtensionModel, "inkcre/mail")
    assert extension is not None
    extension.config = {"default_excluded_mailboxes": {"names": [name], "special_uses": []}}
    db.add(extension)
    db.commit()


def test_mail_collection_backfill_and_materialization(
  async_runner, dovecot: DovecotHarness
) -> None:
  _reset_acceptance_database()
  async_runner.run(_bootstrap_runtime_catalogs())
  dovecot.create_mailbox("Excluded")

  historical_uid = dovecot.append(
    "INBOX",
    CORPUS / "historical-parent.eml",
    internal_date=datetime.datetime(2026, 8, 1, 9, tzinfo=UTC),
  )
  source = async_runner.run(
    SourceManager.create(
      MAIL_SOURCE_TYPE,
      nickname="Real Dovecot acceptance",
      config={
        "protocol": "imap",
        "parameters": {
          "host": "127.0.0.1",
          "port": dovecot.port,
          "security": "plain",
          "username": dovecot.username,
          "password": dovecot.password,
        },
        "excluded_mailboxes": None,
        "ordinary_mark_as_seen": True,
        "backfill_mark_as_seen": False,
        "synchronize_deletions": False,
      },
    )
  )
  assert source.id is not None
  current_date = source.created_at + datetime.timedelta(seconds=5)
  reply_uid = dovecot.append(
    "INBOX",
    CORPUS / "current-reply.eml",
    internal_date=current_date,
    flags=("\\Flagged",),
  )
  removal_uid = dovecot.append(
    "INBOX",
    CORPUS / "removal-candidate.eml",
    internal_date=current_date + datetime.timedelta(seconds=1),
  )
  dovecot.append(
    "Excluded",
    CORPUS / "current-reply.eml",
    internal_date=current_date,
  )

  async def journeys() -> None:
    # J1 — ordinary collection, repeatability, reference completion and flags.
    first = await _run_job(SOURCE_COLLECT_JOB_TYPE, source.id)
    assert first.status == JobStatus.FINISHED
    assert first.state["diagnostics"] == []
    assert first.state["counts"]["messages"] == 2
    inbox = _mailbox("INBOX")
    assert inbox is not None and inbox.id is not None
    assert _mailbox("Excluded") is None
    assert _email("safety-property-historical@inkcre.acceptance") is None

    reply = _email("deep-module-reply@inkcre.acceptance")
    removal = _email("ownership-before-mechanism@inkcre.acceptance")
    sparse_parent = _email("deep-module-parent@inkcre.acceptance")
    assert reply is not None and reply.id is not None
    assert removal is not None and removal.id is not None
    assert sparse_parent is not None and sparse_parent.id is not None
    assert CanonicalEmail.model_validate_json(sparse_parent.content).subject is None
    assert _locator(inbox.id, reply_uid).to_ == reply.id  # type: ignore[union-attr]
    assert _locator(inbox.id, removal_uid).to_ == removal.id  # type: ignore[union-attr]
    assert "\\Seen" in dovecot.flags("INBOX", reply_uid)
    assert {"\\Flagged", "\\Seen"} <= _flag_names(reply.id)

    components = _components(reply.id)
    assert {value["role"] for _, _, value in components} == {
      "body",
      "inline",
      "attachment",
    }
    mime_components = [
      item for item in components if item[1].resolver == MIME_PART_RESOLVER
    ]
    assert len(mime_components) == 2
    with TestSession() as db:
      participant_roles = {
        json.loads(relation.content)["role"]
        for relation in db.exec(
          sqlmodel.select(RelationModel).where(RelationModel.from_ == reply.id)
        ).all()
        if (
          (target := db.get(BlockModel, relation.to_)) is not None
          and target.resolver == EMAIL_ADDRESS_RESOLVER
        )
      }
    assert participant_roles == {"from", "to", "cc"}

    before_repeat = _graph_counts()
    repeated = await _run_job(SOURCE_COLLECT_JOB_TYPE, source.id)
    assert repeated.status == JobStatus.FINISHED
    assert repeated.state["diagnostics"] == []
    assert _graph_counts() == before_repeat

    parent_uid = dovecot.append(
      "INBOX",
      CORPUS / "missing-parent.eml",
      internal_date=current_date + datetime.timedelta(seconds=2),
    )
    dovecot.replace_flags("INBOX", reply_uid, ("\\Answered",))
    changed = await _run_job(SOURCE_COLLECT_JOB_TYPE, source.id)
    assert changed.status == JobStatus.FINISHED
    completed_parent = _email("deep-module-parent@inkcre.acceptance")
    assert completed_parent is not None and completed_parent.id == sparse_parent.id
    assert CanonicalEmail.model_validate_json(completed_parent.content).subject
    assert _locator(inbox.id, parent_uid).to_ == completed_parent.id  # type: ignore[union-attr]
    assert _flag_names(reply.id) == {"\\Answered"}

    # J3 — graph-aware read and explicit, storage-backed MIME materialization.
    solved = await ResolverManager.get(reply).get_solved_content(materialize_missing=False)
    assert isinstance(solved, SolvedEmail)
    assert len(solved.bodies) == 2
    assert len(solved.mime_parts) == 2
    assert all(
      isinstance(part.solved_content, SolvedMimePart)
      and part.solved_content.content is None
      for part in solved.mime_parts
    )
    with TestSession() as db:
      assert len(db.exec(sqlmodel.select(StorageBlobModel)).all()) == 0

    inline = next(block for _, block, value in components if value["role"] == "inline")
    attachment = next(
      block for _, block, value in components if value["role"] == "attachment"
    )
    lexical = await LexicalRetrievalManager.maintain(
      LexicalMaintenanceOptions(max_records=500)
    )
    assert lexical.failed == 0
    attachment_match = await LexicalRetrievalManager.retrieve_local(
      "deep-module-field-note.pdf"
    )
    assert attachment_match.matches[0].block.id == attachment.id
    with TestSession() as db:
      assert len(db.exec(sqlmodel.select(StorageBlobModel)).all()) == 0
      assert not db.exec(
        sqlmodel.select(RelationModel).where(
          RelationModel.from_ == attachment.id,
          RelationModel.content == "content",
        )
      ).all()

    inline_root = CanonicalMimePart.model_validate_json(inline.content)
    assert inline_root.content_id == "architecture-map@inkcre.acceptance"
    materialized = await ResolverManager.get(inline).get_solved_content()
    assert isinstance(materialized, SolvedMimePart)
    assert materialized.content is not None
    assert materialized.content.block.resolver == "core.image.v1"
    assert materialized.content.block.storage == -4
    with TestSession() as db:
      blobs = db.exec(sqlmodel.select(StorageBlobModel)).all()
      assert len(blobs) == 1 and blobs[0].data.startswith(b"\x89PNG")

    configured = MailSourceConfig.model_validate(_source(source.id).config)
    _set_source_config(
      source.id,
      configured.model_copy(
        update={
          "parameters": configured.parameters.model_copy(
            update={"password": "remote-access-disabled"}
          )
        }
      ),
    )
    existing = await ResolverManager.get(inline).get_solved_content()
    assert isinstance(existing, SolvedMimePart) and existing.content is not None
    with pytest.raises(MailMaterializationUnavailable):
      await ResolverManager.get(attachment).get_solved_content()
    _set_source_config(source.id, configured)

    # J2 — exact backfill, inherited policy snapshot and prospective removal.
    checkpoint_before = MailSourceState.model_validate(_source(source.id).state)
    backfill = await _run_job(
      SOURCE_BACKFILL_JOB_TYPE,
      source.id,
      {"since": "2026-08-01", "before": "2026-08-02"},
    )
    assert backfill.status == JobStatus.FINISHED
    assert backfill.state["diagnostics"] == []
    historical = _email("safety-property-historical@inkcre.acceptance")
    assert historical is not None
    assert "\\Seen" not in dovecot.flags("INBOX", historical_uid)
    assert MailSourceState.model_validate(_source(source.id).state) == checkpoint_before

    graph_after_backfill = _graph_counts()
    repeated_backfill = await _run_job(
      SOURCE_BACKFILL_JOB_TYPE,
      source.id,
      {"since": "2026-08-01", "before": "2026-08-02"},
    )
    assert repeated_backfill.status == JobStatus.FINISHED
    assert _graph_counts() == graph_after_backfill
    empty = await _run_job(
      SOURCE_BACKFILL_JOB_TYPE,
      source.id,
      {"since": "2026-08-02", "before": "2026-08-03"},
    )
    assert empty.status == JobStatus.FINISHED and empty.state["messages"] == 0

    inherited = MailSourceConfig.model_validate(_source(source.id).config)
    assert inherited.excluded_mailboxes is not None
    assert inherited.excluded_mailboxes.names == ["Excluded"]
    _set_extension_exclusion("Archive")
    await _run_job(SOURCE_COLLECT_JOB_TYPE, source.id)
    unchanged = MailSourceConfig.model_validate(_source(source.id).config)
    assert unchanged.excluded_mailboxes is not None
    assert unchanged.excluded_mailboxes.names == ["Excluded"]
    _set_source_config(source.id, unchanged.model_copy(update={"excluded_mailboxes": None}))
    await _run_job(SOURCE_COLLECT_JOB_TYPE, source.id)
    rematerialized = MailSourceConfig.model_validate(_source(source.id).config)
    assert rematerialized.excluded_mailboxes is not None
    assert rematerialized.excluded_mailboxes.names == ["Archive"]

    dovecot.expunge("INBOX", removal_uid)
    ignored_removal = await _run_job(SOURCE_COLLECT_JOB_TYPE, source.id)
    assert ignored_removal.status == JobStatus.FINISHED
    assert _locator(inbox.id, removal_uid) is not None

    synchronized = rematerialized.model_copy(update={"synchronize_deletions": True})
    _set_source_config(source.id, synchronized)
    dovecot.expunge("INBOX", parent_uid)
    applied_removal = await _run_job(SOURCE_COLLECT_JOB_TYPE, source.id)
    assert applied_removal.status == JobStatus.FINISHED
    assert _locator(inbox.id, parent_uid) is None
    assert _locator(inbox.id, removal_uid) is not None
    assert _email("deep-module-parent@inkcre.acceptance") is not None
    assert _email("ownership-before-mechanism@inkcre.acceptance") is not None

  async_runner.run(journeys())
