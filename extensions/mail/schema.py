"""Canonical Mail facts, commands, and protocol state."""

from __future__ import annotations

import datetime
import typing

import pydantic


MailProtocol = typing.Literal["imap"]


class IMAPParameters(pydantic.BaseModel):
  """Typed access parameters for one IMAP context."""

  model_config = pydantic.ConfigDict(extra="forbid")

  host: str = pydantic.Field(min_length=1)
  port: int = pydantic.Field(default=993, ge=1, le=65535)
  security: typing.Literal["tls", "starttls", "plain"] = "tls"
  username: str = pydantic.Field(min_length=1)
  password: str = pydantic.Field(min_length=1, json_schema_extra={"format": "password"})


class MailboxExclusionPolicy(pydantic.BaseModel):
  """Exact-name and special-use exclusions; no glob/provider DSL."""

  model_config = pydantic.ConfigDict(extra="forbid")

  names: list[str] = pydantic.Field(default_factory=list)
  special_uses: list[str] = pydantic.Field(
    default_factory=lambda: ["\\Drafts", "\\Junk", "\\Trash"]
  )


class MailSourceConfig(pydantic.BaseModel):
  """Persistent setup policy for one Mail access context."""

  model_config = pydantic.ConfigDict(extra="forbid")

  protocol: MailProtocol = "imap"
  parameters: IMAPParameters
  excluded_mailboxes: MailboxExclusionPolicy | None = None
  ordinary_mark_as_seen: bool = True
  backfill_mark_as_seen: bool = False
  synchronize_deletions: bool = False


class MailCollectConfig(pydantic.BaseModel):
  """Ordinary Mail collection currently needs no per-command policy."""

  model_config = pydantic.ConfigDict(extra="forbid")


class MailBackfillConfig(pydantic.BaseModel):
  """Exact historical collection interval, interpreted as ``[since, before)``."""

  model_config = pydantic.ConfigDict(extra="forbid")

  since: datetime.date
  before: datetime.date | None = None

  @pydantic.model_validator(mode="after")
  def validate_interval(self) -> "MailBackfillConfig":
    if self.before is not None:
      if self.since >= self.before:
        raise ValueError("since must be earlier than before")
    return self


class MailAccessBinding(pydantic.BaseModel):
  """Non-secret continuity evidence for one configured access context."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  protocol: MailProtocol
  host: str
  port: int
  security: typing.Literal["tls", "starttls", "plain"]
  username: str


class IMAPCheckpoint(pydantic.BaseModel):
  """Adapter-interpreted progress for one stable local Mailbox anchor."""

  model_config = pydantic.ConfigDict(extra="forbid")

  uid_validity: int = pydantic.Field(gt=0)
  last_uid: int = pydantic.Field(default=0, ge=0)
  highest_modseq: int | None = pydantic.Field(default=None, ge=1)


class MailSourceState(pydantic.BaseModel):
  """Source-owned binding and per-Mailbox protocol checkpoints."""

  model_config = pydantic.ConfigDict(extra="forbid")

  binding: MailAccessBinding | None = None
  checkpoints: dict[str, IMAPCheckpoint] = pydantic.Field(default_factory=dict)


class CanonicalEmail(pydantic.BaseModel):
  """Intrinsic scalar and identity facts of one best-effort canonical Email."""

  model_config = pydantic.ConfigDict(extra="forbid")

  message_id: str | None = None
  email_id: str | None = None
  subject: str | None = None
  authored_at: datetime.datetime | None = None


class CanonicalMailbox(pydantic.BaseModel):
  """Source-scoped Mailbox projection retained for use and rename continuity."""

  model_config = pydantic.ConfigDict(extra="forbid")

  name: str
  special_uses: tuple[str, ...] = ()
  mailbox_id: str | None = None


class CanonicalMailingList(pydantic.BaseModel):
  """Publisher-declared list identity; not proof of sender authenticity."""

  model_config = pydantic.ConfigDict(extra="forbid")

  list_id: str
  name: str | None = None


class CanonicalEmailAddress(pydantic.BaseModel):
  """Shared canonical addr-spec without occurrence-local display name."""

  model_config = pydantic.ConfigDict(extra="forbid")

  address: str


class CanonicalMailFlag(pydantic.BaseModel):
  """One Mailbox-scoped flag/keyword vocabulary entry."""

  model_config = pydantic.ConfigDict(extra="forbid")

  name: str
  description: str | None = None


class CanonicalMimePart(pydantic.BaseModel):
  """Semantic metadata known before a MIME part's bytes are materialized."""

  model_config = pydantic.ConfigDict(extra="forbid")

  media_type: str
  charset: str | None = None
  filename: str | None = None
  content_id: str | None = None
  description: str | None = None
  transfer_encoding: str | None = None
  encoded_size: int | None = pydantic.Field(default=None, ge=0)
  content_location: str | None = None


class ParticipantFact(pydantic.BaseModel):
  """One decoded address occurrence from a message header."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  role: typing.Literal["from", "sender", "reply_to", "to", "cc", "bcc"]
  order: int = pydantic.Field(ge=0)
  address: str
  display_name: str | None = None


class BodyFact(pydantic.BaseModel):
  """One decoded text body representation."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  part_id: str
  media_type: typing.Literal["text/plain", "text/html"]
  content: str
  alternative_groups: tuple[str, ...] = ()


class MimePartFact(pydantic.BaseModel):
  """One non-body MIME component and its Email-relative role/path."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  part_id: str
  role: typing.Literal["attachment", "inline"]
  metadata: CanonicalMimePart


class MailboxFact(pydantic.BaseModel):
  """Adapter observation needed to reconcile one source-scoped Mailbox."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  mailbox: CanonicalMailbox
  uid_validity: int = pydantic.Field(gt=0)


class MessageFact(pydantic.BaseModel):
  """Protocol-neutral accepted input for one exact remote occurrence."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  uid: int = pydantic.Field(gt=0)
  uid_validity: int = pydantic.Field(gt=0)
  internal_date: datetime.datetime | None = None
  root: CanonicalEmail
  mailing_list: CanonicalMailingList | None = None
  participants: tuple[ParticipantFact, ...] = ()
  bodies: tuple[BodyFact, ...] = ()
  mime_parts: tuple[MimePartFact, ...] = ()
  in_reply_to: tuple[str, ...] = ()
  references: tuple[str, ...] = ()
  flags: tuple[str, ...] = ()
  modseq: int | None = pydantic.Field(default=None, ge=1)


class FlagChangeFact(pydantic.BaseModel):
  """Flag snapshot for an already known exact occurrence."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  uid: int = pydantic.Field(gt=0)
  uid_validity: int = pydantic.Field(gt=0)
  flags: tuple[str, ...] = ()
  modseq: int | None = pydantic.Field(default=None, ge=1)


class MailboxChanges(pydantic.BaseModel):
  """One adapter batch plus its proposed checkpoint."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  messages: tuple[MessageFact, ...] = ()
  flag_changes: tuple[FlagChangeFact, ...] = ()
  removed_uids: tuple[int, ...] = ()
  next_checkpoint: IMAPCheckpoint


class ContainsRelation(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  type: typing.Literal["contains"] = "contains"
  uid_validity: int = pydantic.Field(gt=0)
  uid: int = pydantic.Field(gt=0)


class ComponentRelation(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  role: typing.Literal["body", "attachment", "inline"]
  part_id: str
  alternative_groups: tuple[str, ...] | None = None


class ParticipantRelation(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  role: typing.Literal["from", "sender", "reply_to", "to", "cc", "bcc"]
  order: int = pydantic.Field(ge=0)
  display_name: str | None = None


class EmbeddedReferenceRelation(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  type: typing.Literal["embeds"] = "embeds"
  reference: str


class SolvedBlock(pydantic.BaseModel):
  """One related Block and its direct semantic resolution."""

  model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

  block: typing.Any
  solved_content: typing.Any


class SolvedBody(SolvedBlock):
  relation: ComponentRelation


class SolvedMailingList(pydantic.BaseModel):
  root: CanonicalMailingList
  emails: tuple[SolvedBlock, ...] = ()


class SolvedMimePart(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

  root: CanonicalMimePart
  content: SolvedBlock | None = None


class SolvedEmail(pydantic.BaseModel):
  """Graph-aware use projection assembled by the Email Resolver."""

  model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

  root: CanonicalEmail
  bodies: tuple[SolvedBody, ...] = ()
  mailing_lists: tuple[SolvedBlock, ...] = ()
  mime_parts: tuple[SolvedBlock, ...] = ()
  participants: tuple[dict[str, typing.Any], ...] = ()
  mailboxes: tuple[dict[str, typing.Any], ...] = ()
  flags: tuple[dict[str, typing.Any], ...] = ()
  parents: tuple[SolvedBlock, ...] = ()
  references: tuple[SolvedBlock, ...] = ()


class MimePartMaterializeRequest(pydantic.BaseModel):
  """Exact Peer command input; provenance and routing remain graph-derived."""

  model_config = pydantic.ConfigDict(extra="forbid")

  block: int
