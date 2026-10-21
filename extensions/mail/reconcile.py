"""Mail-owned reconciliation and canonical graph effects."""

from __future__ import annotations

import json
import re
import typing

import pydantic

from app.business.source import SourceManager
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.info_base.relation import RelationModel
from app.schemas.source import SourceModel
from app.persistence.source.uow import SourceUnitOfWork

from .schema import (
  CanonicalEmail,
  CanonicalEmailAddress,
  CanonicalMailFlag,
  ComponentRelation,
  ContainsRelation,
  EmbeddedReferenceRelation,
  MailboxFact,
  MessageFact,
  ParticipantRelation,
)
from .newsletter import (
  HTML_BODY_RESOLVERS,
  MAILING_LIST_RESOLVER,
  NEWSLETTER_HTML_RESOLVER,
  NEWSLETTER_TEXT_RESOLVER,
  TEXT_BODY_RESOLVERS,
)


EMAIL_RESOLVER = "extensions.mail.email.v1"
MAILBOX_RESOLVER = "extensions.mail.mailbox.v1"
EMAIL_ADDRESS_RESOLVER = "extensions.mail.email_address.v1"
MAIL_FLAG_RESOLVER = "extensions.mail.flag.v1"
MIME_PART_RESOLVER = "extensions.mail.mime_part.v1"
TEXT_RESOLVER = "core.text.v1"
HTML_RESOLVER = "core.html.v1"

_HTML_REFERENCE = re.compile(
  r"(?:src|href)\s*=\s*['\"]\s*([^'\"\s>]+)",
  re.IGNORECASE,
)

_FLAG_DESCRIPTIONS = {
  "\\seen": "The message has been read.",
  "\\answered": "The message has been answered.",
  "\\flagged": "The message is marked for special attention.",
  "\\deleted": "The message is marked for removal from this mailbox.",
  "\\draft": "The message is a draft.",
}


def compact_json(value: pydantic.BaseModel | dict[str, typing.Any]) -> str:
  payload = (
    value.model_dump(mode="json") if isinstance(value, pydantic.BaseModel) else value
  )
  return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _persisted_id(block: BlockModel) -> int:
  if block.id is None:  # pragma: no cover - database invariant
    raise RuntimeError("Persisted Block has no ID")
  return block.id


def _json(content: str) -> dict[str, typing.Any] | None:
  try:
    value = json.loads(content)
  except (TypeError, json.JSONDecodeError):
    return None
  return value if isinstance(value, dict) else None


class MailGraphReconciler:
  """Own Mail's linear reconciliation ladder and graph grammar."""

  def __init__(self, uow: SourceUnitOfWork, source_block: BlockModel):
    self.uow = uow
    self.source_block = source_block

  @classmethod
  async def for_source(cls, uow: SourceUnitOfWork, source: SourceModel):
    return cls(uow, await SourceManager.ensure_block_async(source, uow))

  async def ensure_mailbox(self, fact: MailboxFact) -> BlockModel:
    source_block_id = _persisted_id(self.source_block)
    managed_relations = await self.uow.graph.relations.get(
      source_block_id, include_in=False, content="manages"
    )
    candidates = [
      block
      for relation in managed_relations
      if (block := await self.uow.graph.blocks.get(relation.to_)) is not None
      and block.resolver == MAILBOX_RESOLVER
    ]
    mailbox_id = fact.mailbox.mailbox_id
    if mailbox_id is not None:
      matches = [
        block
        for block in candidates
        if (_json(block.content) or {}).get("mailbox_id") == mailbox_id
      ]
    else:
      matches = [
        block
        for block in candidates
        if (_json(block.content) or {}).get("mailbox_id") is None
        and (_json(block.content) or {}).get("name") == fact.mailbox.name
      ]
    mailbox = (
      matches[0]
      if len(matches) == 1
      else await self.uow.graph.blocks.create(
        BlockForm(resolver=MAILBOX_RESOLVER, content=fact.mailbox.model_dump_json()),
      )
    )
    canonical = fact.mailbox.model_dump_json()
    if mailbox.content != canonical:
      mailbox.content = canonical
      await self.uow.graph.blocks.save(mailbox)
    await self.uow.graph.relations.fetchsert(
      RelationModel(
        from_=source_block_id,
        to_=_persisted_id(mailbox),
        content="manages",
      ),
    )
    return mailbox

  async def clear_stale_epoch(self, mailbox_id: int, uid_validity: int) -> None:
    """Remove locators and their scoped flag applications from an invalid epoch."""
    relations = await self.uow.graph.relations.get(mailbox_id, include_in=False)
    stale_email_ids: set[int] = set()
    for relation in relations:
      value = _json(relation.content)
      if (
        value
        and value.get("type") == "contains"
        and value.get("uid_validity") != uid_validity
      ):
        stale_email_ids.add(relation.to_)
        await self.uow.graph.relations.remove(relation)
    if stale_email_ids:
      await self._delete_scoped_tags(mailbox_id, stale_email_ids)

  async def reconcile_message(self, mailbox: BlockModel, fact: MessageFact) -> BlockModel:
    mailbox_id = _persisted_id(mailbox)
    email_block = await self._locate_email(mailbox_id, fact)
    if email_block is None:
      email_block = await self.uow.graph.blocks.create(
        BlockForm(resolver=EMAIL_RESOLVER, content=fact.root.model_dump_json()),
      )
    else:
      await self._complete_email(email_block, fact.root)

    email_id = _persisted_id(email_block)
    contains = ContainsRelation(uid_validity=fact.uid_validity, uid=fact.uid)
    existing_pair = await self.uow.graph.relations.get_outgoing_many(
      (mailbox_id,), to_ids=(email_id,)
    )
    for relation in existing_pair:
      value = _json(relation.content)
      if value and value.get("type") == "contains":
        relation.content = compact_json(contains)
        await self.uow.graph.relations.save(relation)
        break
    else:
      await self.uow.graph.relations.create(
        mailbox_id,
        email_id,
        compact_json(contains),
      )

    part_blocks, html_bodies = await self._reconcile_components(email_block, fact)
    await self._reconcile_mailing_list(email_block, fact)
    await self._reconcile_participants(email_block, fact)
    await self._reconcile_references(email_block, fact)
    await self.replace_flags(mailbox, email_block, fact.flags)
    await self._reconcile_embedded_references(html_bodies, part_blocks)
    return email_block

  async def _reconcile_mailing_list(
    self, email_block: BlockModel, fact: MessageFact
  ) -> None:
    if fact.mailing_list is None:
      return
    matches = await self.uow.graph.blocks.find_json_field(
      MAILING_LIST_RESOLVER, "list_id", fact.mailing_list.list_id
    )
    publication = (
      min(matches, key=_persisted_id)
      if matches
      else await self.uow.graph.blocks.create(
        BlockForm(
          resolver=MAILING_LIST_RESOLVER, content=fact.mailing_list.model_dump_json()
        )
      )
    )
    await self.uow.graph.relations.fetchsert(
      RelationModel(
        from_=_persisted_id(publication),
        to_=_persisted_id(email_block),
        content="publishes",
      )
    )

  async def reconcile_flag_change(
    self,
    mailbox: BlockModel,
    uid_validity: int,
    uid: int,
    flags: tuple[str, ...],
  ) -> bool:
    email = await self._email_by_locator(_persisted_id(mailbox), uid_validity, uid)
    if email is None:
      return False
    await self.replace_flags(mailbox, email, flags)
    return True

  async def remove_occurrence(
    self,
    mailbox: BlockModel,
    uid_validity: int,
    uid: int,
  ) -> bool:
    mailbox_id = _persisted_id(mailbox)
    target = await self._occurrence_relation(mailbox_id, uid_validity, uid)
    if target is None:
      return False
    email_id = target.to_
    await self.uow.graph.relations.remove(target)
    await self._delete_scoped_tags(mailbox_id, {email_id})
    return True

  async def replace_flags(
    self,
    mailbox: BlockModel,
    email_block: BlockModel,
    flags: tuple[str, ...],
  ) -> None:
    mailbox_id = _persisted_id(mailbox)
    email_id = _persisted_id(email_block)
    await self._delete_scoped_tags(mailbox_id, {email_id})
    for name in sorted(set(flags), key=str.casefold):
      if name.casefold() == "\\recent":
        continue
      flag = await self._ensure_flag(mailbox_id, name)
      await self.uow.graph.relations.fetchsert(
        RelationModel(from_=_persisted_id(flag), to_=email_id, content="tags"),
      )

  async def _ensure_flag(self, mailbox_id: int, name: str) -> BlockModel:
    flag_relations = await self.uow.graph.relations.get(
      mailbox_id, include_in=False, content="has"
    )
    normalized = name.casefold()
    matches = [
      block
      for relation in flag_relations
      if (block := await self.uow.graph.blocks.get(relation.to_)) is not None
      and block.resolver == MAIL_FLAG_RESOLVER
      and str((_json(block.content) or {}).get("name", "")).casefold() == normalized
    ]
    if len(matches) == 1:
      return matches[0]
    content = CanonicalMailFlag(
      name=name,
      description=_FLAG_DESCRIPTIONS.get(normalized),
    )
    flag = await self.uow.graph.blocks.create(
      BlockForm(resolver=MAIL_FLAG_RESOLVER, content=content.model_dump_json()),
    )
    await self.uow.graph.relations.create(mailbox_id, _persisted_id(flag), "has")
    return flag

  async def _delete_scoped_tags(self, mailbox_id: int, email_ids: set[int]) -> None:
    if not email_ids:
      return
    owned_flag_ids = [
      r.to_
      for r in await self.uow.graph.relations.get(
        mailbox_id, include_in=False, content="has"
      )
    ]
    if not owned_flag_ids:
      return
    tags = await self.uow.graph.relations.get_outgoing_many(
      owned_flag_ids, to_ids=email_ids, content="tags"
    )
    for relation in tags:
      await self.uow.graph.relations.remove(relation)

  async def _locate_email(self, mailbox_id: int, fact: MessageFact) -> BlockModel | None:
    exact = await self._email_by_locator(mailbox_id, fact.uid_validity, fact.uid)
    if exact is not None:
      return exact if self._identity_compatible(exact, fact.root) else None

    scoped = (
      await self._source_scoped_candidates("email_id", fact.root.email_id)
      if fact.root.email_id
      else []
    )
    selected = await self._select_candidate(scoped, mailbox_id, fact, strong=True)
    if selected is not None:
      return selected

    message_candidates = (
      await self._global_candidates("message_id", fact.root.message_id)
      if fact.root.message_id
      else []
    )
    return await self._select_candidate(message_candidates, mailbox_id, fact, strong=False)

  async def _select_candidate(
    self,
    candidates: list[BlockModel],
    mailbox_id: int,
    fact: MessageFact,
    *,
    strong: bool,
  ) -> BlockModel | None:
    compatible = [
      candidate
      for candidate in candidates
      if self._identity_compatible(candidate, fact.root)
      and not await self._has_other_uid_in_mailbox(
        mailbox_id,
        _persisted_id(candidate),
        fact.uid_validity,
        fact.uid,
      )
      and (strong or await self._message_id_reuse_safe(candidate, fact))
    ]
    return compatible[0] if len(compatible) == 1 else None

  async def _message_id_reuse_safe(self, candidate: BlockModel, fact: MessageFact) -> bool:
    if not fact.mime_parts:
      return True
    candidate_id = _persisted_id(candidate)
    incoming = await self.uow.graph.relations.get(candidate_id, include_out=False)
    if any(
      value.get("type") == "contains"
      for relation in incoming
      if (value := _json(relation.content)) is not None
    ):
      return False
    value = CanonicalEmail.model_validate_json(candidate.content)
    return value.email_id is None and value.subject is None and value.authored_at is None

  def _identity_compatible(self, block: BlockModel, root: CanonicalEmail) -> bool:
    current = CanonicalEmail.model_validate_json(block.content)
    return all(
      previous is None or incoming is None or previous == incoming
      for previous, incoming in (
        (current.message_id, root.message_id),
        (current.email_id, root.email_id),
      )
    )

  async def _complete_email(self, block: BlockModel, root: CanonicalEmail) -> None:
    current = CanonicalEmail.model_validate_json(block.content)
    merged = CanonicalEmail(
      message_id=current.message_id or root.message_id,
      email_id=current.email_id or root.email_id,
      subject=root.subject if root.subject is not None else current.subject,
      authored_at=(
        root.authored_at if root.authored_at is not None else current.authored_at
      ),
    )
    content = merged.model_dump_json()
    if block.content != content:
      block.content = content
      await self.uow.graph.blocks.save(block)

  async def _occurrence_relation(
    self,
    mailbox_id: int,
    uid_validity: int,
    uid: int,
  ) -> RelationModel | None:
    relations = await self.uow.graph.relations.get(mailbox_id, include_in=False)
    matches = [
      relation
      for relation in relations
      if (value := _json(relation.content))
      and value.get("type") == "contains"
      and value.get("uid_validity") == uid_validity
      and value.get("uid") == uid
    ]
    return matches[0] if len(matches) == 1 else None

  async def _email_by_locator(
    self,
    mailbox_id: int,
    uid_validity: int,
    uid: int,
  ) -> BlockModel | None:
    relation = await self._occurrence_relation(mailbox_id, uid_validity, uid)
    if relation is None:
      return None
    block = await self.uow.graph.blocks.get(relation.to_)
    return block if block is not None and block.resolver == EMAIL_RESOLVER else None

  async def _has_other_uid_in_mailbox(
    self,
    mailbox_id: int,
    email_id: int,
    uid_validity: int,
    uid: int,
  ) -> bool:
    relations = await self.uow.graph.relations.get_outgoing_many(
      (mailbox_id,), to_ids=(email_id,)
    )
    return any(
      value.get("type") == "contains"
      and (value.get("uid_validity"), value.get("uid")) != (uid_validity, uid)
      for relation in relations
      if (value := _json(relation.content)) is not None
    )

  async def _global_candidates(self, field: str, value: str | None) -> list[BlockModel]:
    if value is None:
      return []
    return list(await self.uow.graph.blocks.find_json_field(EMAIL_RESOLVER, field, value))

  async def _source_scoped_candidates(self, field: str, value: str) -> list[BlockModel]:
    source_block_id = _persisted_id(self.source_block)
    mailbox_ids = [
      r.to_
      for r in await self.uow.graph.relations.get(
        source_block_id, include_in=False, content="manages"
      )
    ]
    if not mailbox_ids:
      return []
    email_ids = [
      r.to_ for r in await self.uow.graph.relations.get_outgoing_many(mailbox_ids)
    ]
    if not email_ids:
      return []
    return list(
      await self.uow.graph.blocks.find_json_field(
        EMAIL_RESOLVER, field, value, block_ids=email_ids
      )
    )

  async def _reconcile_components(
    self,
    email_block: BlockModel,
    fact: MessageFact,
  ) -> tuple[dict[str, BlockModel], list[tuple[BlockModel, str]]]:
    email_id = _persisted_id(email_block)
    existing = await self.uow.graph.relations.get(email_id, include_in=False)
    by_part_id = {
      str(value["part_id"]): relation
      for relation in existing
      if (value := _json(relation.content))
      and value.get("role") in {"body", "attachment", "inline"}
      and "part_id" in value
    }
    part_blocks: dict[str, BlockModel] = {}
    html_bodies: list[tuple[BlockModel, str]] = []
    for body in fact.bodies:
      resolver = TEXT_RESOLVER if body.media_type == "text/plain" else HTML_RESOLVER
      if fact.mailing_list is not None:
        resolver = (
          NEWSLETTER_TEXT_RESOLVER
          if body.media_type == "text/plain"
          else NEWSLETTER_HTML_RESOLVER
        )
      relation = by_part_id.get(body.part_id)
      block = (
        await self.uow.graph.blocks.get(relation.to_) if relation is not None else None
      )
      component = compact_json(
        ComponentRelation(
          role="body",
          part_id=body.part_id,
          alternative_groups=body.alternative_groups,
        )
      )
      compatible = (
        TEXT_BODY_RESOLVERS if body.media_type == "text/plain" else HTML_BODY_RESOLVERS
      )
      if block is None or block.resolver not in compatible:
        block = await self.uow.graph.blocks.create(
          BlockForm(resolver=resolver, content=body.content),
        )
        await self.uow.graph.relations.fetchsert(
          RelationModel(
            from_=email_id,
            to_=_persisted_id(block),
            content=component,
          ),
        )
      elif (
        block.content != body.content
        or block.storage is not None
        or (fact.mailing_list is not None and block.resolver != resolver)
      ):
        block.storage = None
        block.content = body.content
        block.resolver = resolver
        await self.uow.graph.blocks.save(block)
      if relation is not None and relation.content != component:
        relation.content = component
        await self.uow.graph.relations.save(relation)
      part_blocks[body.part_id] = block
      if resolver in HTML_BODY_RESOLVERS:
        html_bodies.append((block, body.content))

    for part in fact.mime_parts:
      relation = by_part_id.get(part.part_id)
      block = (
        await self.uow.graph.blocks.get(relation.to_) if relation is not None else None
      )
      content = part.metadata.model_dump_json()
      if block is None or block.resolver != MIME_PART_RESOLVER:
        block = await self.uow.graph.blocks.create(
          BlockForm(resolver=MIME_PART_RESOLVER, content=content),
        )
        await self.uow.graph.relations.fetchsert(
          RelationModel(
            from_=email_id,
            to_=_persisted_id(block),
            content=compact_json(ComponentRelation(role=part.role, part_id=part.part_id)),
          ),
        )
      elif block.content != content:
        block.content = content
        await self.uow.graph.blocks.save(block)
      part_blocks[part.part_id] = block
    return part_blocks, html_bodies

  async def _reconcile_participants(
    self, email_block: BlockModel, fact: MessageFact
  ) -> None:
    email_id = _persisted_id(email_block)
    existing = await self.uow.graph.relations.get(email_id, include_in=False)
    for relation in existing:
      value = _json(relation.content)
      if value and value.get("role") in {
        "from",
        "sender",
        "reply_to",
        "to",
        "cc",
        "bcc",
      }:
        await self.uow.graph.relations.remove(relation)
    for participant in fact.participants:
      address_content = CanonicalEmailAddress(address=participant.address)
      matches = await self.uow.graph.blocks.find_json_field(
        EMAIL_ADDRESS_RESOLVER, "address", participant.address
      )
      address_block = (
        matches[0]
        if len(matches) == 1
        else await self.uow.graph.blocks.create(
          BlockForm(
            resolver=EMAIL_ADDRESS_RESOLVER,
            content=address_content.model_dump_json(),
          ),
        )
      )
      relation = ParticipantRelation(
        role=participant.role,
        order=participant.order,
        display_name=participant.display_name,
      )
      await self.uow.graph.relations.fetchsert(
        RelationModel(
          from_=email_id,
          to_=_persisted_id(address_block),
          content=compact_json(relation),
        ),
      )

  async def _reconcile_references(self, email_block: BlockModel, fact: MessageFact) -> None:
    email_id = _persisted_id(email_block)
    existing = await self.uow.graph.relations.get(email_id, include_in=False)
    for relation in existing:
      if relation.content.startswith("parent:") or relation.content.startswith(
        "reference:"
      ):
        await self.uow.graph.relations.remove(relation)
    for prefix, message_ids in (
      ("parent", fact.in_reply_to),
      ("reference", fact.references),
    ):
      for order, message_id in enumerate(message_ids):
        candidates = await self._global_candidates("message_id", message_id)
        target = (
          candidates[0]
          if len(candidates) == 1
          else await self.uow.graph.blocks.create(
            BlockForm(
              resolver=EMAIL_RESOLVER,
              content=CanonicalEmail(message_id=message_id).model_dump_json(),
            ),
          )
        )
        await self.uow.graph.relations.fetchsert(
          RelationModel(
            from_=email_id,
            to_=_persisted_id(target),
            content=f"{prefix}:{order}",
          ),
        )

  async def _reconcile_embedded_references(
    self,
    html_bodies: list[tuple[BlockModel, str]],
    parts: dict[str, BlockModel],
  ) -> None:
    labeled_parts: dict[str, BlockModel] = {}
    for block in parts.values():
      if block.resolver != MIME_PART_RESOLVER:
        continue
      value = _json(block.content) or {}
      content_id = value.get("content_id")
      location = value.get("content_location")
      if content_id:
        labeled_parts[f"cid:{str(content_id).casefold()}"] = block
      if location:
        labeled_parts[str(location)] = block
    for html_block, source in html_bodies:
      html_id = _persisted_id(html_block)
      for reference in _HTML_REFERENCE.findall(source):
        target = labeled_parts.get(reference.casefold()) or labeled_parts.get(reference)
        if target is None:
          continue
        await self.uow.graph.relations.fetchsert(
          RelationModel(
            from_=html_id,
            to_=_persisted_id(target),
            content=compact_json(EmbeddedReferenceRelation(reference=reference)),
          ),
        )
