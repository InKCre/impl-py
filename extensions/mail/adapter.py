"""Protocol-neutral Mail access over the public IMAP protocol."""

from __future__ import annotations

import asyncio
import base64
import binascii
import datetime
import email
import email.header
import email.policy
import email.utils
import quopri
import re
import typing
from urllib.parse import urlsplit, urlunsplit

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError
from imapclient.imapclient import SocketTimeout
import pydantic

from .schema import (
  BodyFact,
  CanonicalEmail,
  CanonicalMailbox,
  CanonicalMailingList,
  CanonicalMimePart,
  FlagChangeFact,
  IMAPCheckpoint,
  IMAPParameters,
  MailAccessBinding,
  MailBackfillConfig,
  MailProtocol,
  MailboxExclusionPolicy,
  MailboxChanges,
  MailboxFact,
  MessageFact,
  MimePartFact,
  ParticipantFact,
)


_MESSAGE_ID = re.compile(r"<\s*([^<>\s]+)\s*>")
_SPECIAL_USES = frozenset({"\\Sent", "\\Drafts", "\\Junk", "\\Trash", "\\Archive"})


class MailAdapterError(RuntimeError):
  """Remote Mail access failed without leaking protocol mechanics upstream."""


def _text(value: typing.Any) -> str | None:
  if value is None:
    return None
  if isinstance(value, bytes):
    return value.decode("utf-8", errors="replace")
  return str(value)


def _normalized_token(value: typing.Any) -> str | None:
  rendered = _text(value)
  return None if rendered is None else rendered.strip()


def _number(value: typing.Any) -> int:
  if isinstance(value, (tuple, list)) and len(value) == 1:
    value = value[0]
  return int(value)


def _normalized_internal_date(value: datetime.datetime) -> datetime.datetime:
  """Restore the local offset IMAPClient intentionally strips, then use UTC."""
  if value.tzinfo is None:
    local_timezone = datetime.datetime.now().astimezone().tzinfo
    value = value.replace(tzinfo=local_timezone)
  return value.astimezone(datetime.timezone.utc)


def _parameters(value: typing.Any) -> dict[str, str]:
  if not isinstance(value, (tuple, list)):
    return {}
  result: dict[str, str] = {}
  iterator = iter(value)
  for key in iterator:
    item = next(iterator, None)
    key_text = _normalized_token(key)
    item_text = _normalized_token(item)
    if key_text and item_text is not None:
      result[key_text.lower()] = item_text
  return result


def _decode_header(value: str | None) -> str | None:
  if value is None:
    return None
  parts: list[str] = []
  for item, charset in email.header.decode_header(value):
    if isinstance(item, bytes):
      try:
        parts.append(item.decode(charset or "utf-8", errors="replace"))
      except LookupError:
        parts.append(item.decode("utf-8", errors="replace"))
    else:
      parts.append(item)
  rendered = "".join(parts).strip()
  return rendered or None


def _message_ids(value: str | None) -> tuple[str, ...]:
  if not value:
    return ()
  return tuple(match.group(1).strip() for match in _MESSAGE_ID.finditer(value))


def _mailing_list(message: email.message.Message) -> CanonicalMailingList | None:
  values = message.get_all("List-ID", [])
  if not values:
    return _beehiiv_publication(message)
  if len(values) != 1:
    return None
  match = re.fullmatch(r"\s*([^<>]*)<([^<>]+)>\s*", str(values[0]))
  if match is None:
    return None
  list_id = re.sub(r"\s+", "", match[2]).lower()
  if len(list_id) > 255 or not re.fullmatch(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)+",
    list_id,
  ):
    return None
  return CanonicalMailingList(
    list_id=list_id, name=_decode_header(match[1].strip().strip('"'))
  )


def _beehiiv_publication(message: email.message.Message) -> CanonicalMailingList | None:
  """Use the declared publication URL, never an issue or subscriber URL."""
  markers = message.get_all("X-Beehiiv-Type", [])
  identities = message.get_all("X-Newsletter-ID", [])
  if len(markers) != 1 or str(markers[0]).strip().lower() != "newsletter":
    return None
  if len(identities) != 1:
    return None
  identity = str(identities[0]).strip()
  if re.search(r"\s", identity):
    return None
  try:
    url = urlsplit(identity)
    port = url.port
    host = url.hostname
  except ValueError:
    return None
  if (
    url.scheme not in {"https", "http"}
    or not host
    or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)
    or url.username is not None
    or url.password is not None
    or url.query
    or url.fragment
  ):
    return None
  authority = host
  if port is not None and (url.scheme, port) not in {("https", 443), ("http", 80)}:
    authority = f"{host}:{port}"
  canonical = urlunsplit((url.scheme, authority, url.path or "/", "", ""))
  return CanonicalMailingList(list_id=f"beehiiv:{canonical}", name=host)


def _canonical_address(address: str) -> str:
  local, separator, domain = address.strip().partition("@")
  if not separator or not local or not domain:
    raise ValueError(f"Invalid email address: {address!r}")
  if domain.startswith("[") and domain.endswith("]"):
    canonical_domain = domain.lower()
  else:
    canonical_domain = domain.encode("idna").decode("ascii").lower()
  return f"{local}@{canonical_domain}"


def _participants(message: email.message.Message) -> tuple[ParticipantFact, ...]:
  result: list[ParticipantFact] = []
  header_roles = (
    ("From", "from"),
    ("Sender", "sender"),
    ("Reply-To", "reply_to"),
    ("To", "to"),
    ("Cc", "cc"),
    ("Bcc", "bcc"),
  )
  for header, role in header_roles:
    values = message.get_all(header, [])
    for order, (name, address) in enumerate(email.utils.getaddresses(values)):
      if not address:
        continue
      try:
        canonical = _canonical_address(address)
      except ValueError:
        continue
      result.append(
        ParticipantFact(
          role=typing.cast(typing.Any, role),
          order=order,
          address=canonical,
          display_name=_decode_header(name),
        )
      )
  return tuple(result)


def decode_transfer(payload: bytes, encoding: str | None) -> bytes:
  normalized = (encoding or "").lower()
  if normalized == "base64":
    try:
      return base64.b64decode(payload, validate=False)
    except binascii.Error:
      return payload
  if normalized == "quoted-printable":
    return quopri.decodestring(payload)
  return payload


def _response_value(response: dict[typing.Any, typing.Any], prefix: bytes):
  for key, value in response.items():
    if isinstance(key, bytes) and key.upper().startswith(prefix):
      return value
  return None


def _parse_uid_set(value: typing.Any) -> tuple[int, ...]:
  """Expand one server-authored UID set after IMAPClient parsed its response line."""
  rendered = (_text(value) or "").strip()
  if rendered.startswith("(") and ")" in rendered:
    rendered = rendered.partition(")")[2].strip()
  result: set[int] = set()
  for item in rendered.split(","):
    start, separator, end = item.strip().partition(":")
    if not start.isdecimal() or (separator and not end.isdecimal()):
      continue
    first = int(start)
    last = int(end) if separator else first
    result.update(range(min(first, last), max(first, last) + 1))
  return tuple(sorted(result))


class _PartDescriptor(pydantic.BaseModel):
  part_id: str
  media_type: str
  charset: str | None = None
  filename: str | None = None
  content_id: str | None = None
  description: str | None = None
  transfer_encoding: str | None = None
  encoded_size: int | None = None
  content_location: str | None = None
  disposition: str | None = None
  alternative_groups: tuple[str, ...] = ()

  @property
  def is_body(self) -> bool:
    return (
      self.media_type in {"text/plain", "text/html"}
      and self.disposition != "attachment"
      and self.filename is None
    )


def _walk_body_structure(
  body: typing.Any,
  prefix: str = "",
  alternative_groups: tuple[str, ...] = (),
) -> tuple[_PartDescriptor, ...]:
  if not isinstance(body, (tuple, list)) or not body:
    return ()
  if isinstance(body[0], list):
    if len(body) > 1 and (_text(body[1]) or "").lower() == "alternative":
      alternative_groups = (*alternative_groups, prefix or "root")
    parts: list[_PartDescriptor] = []
    for index, child in enumerate(body[0], start=1):
      part_id = f"{prefix}.{index}" if prefix else str(index)
      parts.extend(_walk_body_structure(child, part_id, alternative_groups))
    return tuple(parts)

  part_id = prefix or "1"
  media_type = (
    f"{(_text(body[0]) or 'application').lower()}/"
    f"{(_text(body[1]) or 'octet-stream').lower()}"
  )
  params = _parameters(body[2] if len(body) > 2 else None)
  content_id = (_normalized_token(body[3]) if len(body) > 3 else None) or None
  if content_id and content_id.startswith("<") and content_id.endswith(">"):
    content_id = content_id[1:-1].strip()
  description = _decode_header(_text(body[4]) if len(body) > 4 else None)
  transfer_encoding = _normalized_token(body[5]) if len(body) > 5 else None
  encoded_size = body[6] if len(body) > 6 and isinstance(body[6], int) else None

  extension_start = 8 if media_type.startswith("text/") else 7
  disposition: str | None = None
  disposition_params: dict[str, str] = {}
  if len(body) > extension_start + 1:
    raw_disposition = body[extension_start + 1]
    if isinstance(raw_disposition, (tuple, list)) and raw_disposition:
      disposition = (_text(raw_disposition[0]) or "").lower() or None
      disposition_params = _parameters(
        raw_disposition[1] if len(raw_disposition) > 1 else None
      )
  filename = disposition_params.get("filename") or params.get("name")
  content_location = None
  if len(body) > extension_start + 3:
    location = body[extension_start + 3]
    if isinstance(location, (bytes, str)):
      content_location = _normalized_token(location)
  return (
    _PartDescriptor(
      part_id=part_id,
      media_type=media_type,
      charset=params.get("charset"),
      filename=_decode_header(filename),
      content_id=content_id,
      description=description,
      transfer_encoding=(transfer_encoding or "").lower() or None,
      encoded_size=encoded_size,
      content_location=content_location,
      disposition=disposition,
      alternative_groups=alternative_groups,
    ),
  )


class IMAPAdapter:
  """One short-lived, serialized async facade over IMAPClient."""

  def __init__(self, parameters: IMAPParameters):
    self.parameters = parameters
    self.binding = MailAccessBinding(
      protocol="imap",
      host=parameters.host.strip().lower(),
      port=parameters.port,
      security=parameters.security,
      username=parameters.username,
    )
    self._client: IMAPClient | None = None
    self._qresync_enabled = False
    self._lock = asyncio.Lock()

  async def __aenter__(self) -> "IMAPAdapter":
    try:
      await self._run(self._connect)
    except BaseException:
      # Python does not call __aexit__ when entering the context fails/cancels.
      await self._run(self._disconnect)
      raise
    return self

  async def __aexit__(self, exc_type, exc, traceback) -> None:
    del exc_type, exc, traceback
    await self._run(self._disconnect)

  async def _run(self, function, *args):
    async with self._lock:
      operation = asyncio.create_task(asyncio.to_thread(function, *args))
      cancellation: asyncio.CancelledError | None = None
      # Cancelling to_thread's await does not stop its thread. Keep the lock and
      # drain this same call before disconnect or another IMAP command can run.
      while not operation.done():
        try:
          await asyncio.shield(operation)
        except asyncio.CancelledError as error:
          cancellation = error
        except Exception:
          break  # Retrieve the original exception below, once the call has ended.
      try:
        result = operation.result()
      except Exception as error:
        if cancellation is not None:
          raise cancellation from error
        if isinstance(error, (IMAPClientError, OSError)):
          raise MailAdapterError(str(error)) from error
        raise
      if cancellation is not None:
        raise cancellation
      return result

  def _connect(self) -> None:
    use_tls = self.parameters.security == "tls"
    client = IMAPClient(
      self.parameters.host,
      port=self.parameters.port,
      ssl=use_tls,
      # IMAPClient supports its own SocketTimeout despite its narrower annotation.
      timeout=SocketTimeout(connect=15, read=30),  # pyrefly: ignore[bad-argument-type]
    )
    try:
      if self.parameters.security == "starttls":
        client.starttls()
      client.login(self.parameters.username, self.parameters.password)
      capabilities = {(_text(item) or "").upper() for item in client.capabilities()}
      if {"ENABLE", "QRESYNC"} <= capabilities:
        self._qresync_enabled = b"QRESYNC" in client.enable("QRESYNC")
      self._client = client
    except Exception:
      client.shutdown()
      raise

  def _disconnect(self) -> None:
    client, self._client = self._client, None
    if client is None:
      return
    try:
      client.logout()
    except Exception:
      client.shutdown()

  def _connected(self) -> IMAPClient:
    if self._client is None:
      raise MailAdapterError("Mail adapter is not connected")
    return self._client

  async def discover_mailboxes(
    self,
    excluded: MailboxExclusionPolicy,
  ) -> tuple[MailboxFact, ...]:
    return await self._run(self._discover_mailboxes, excluded)

  def _discover_mailboxes(
    self,
    excluded: MailboxExclusionPolicy,
  ) -> tuple[MailboxFact, ...]:
    client = self._connected()
    capabilities = {(_text(item) or "").upper() for item in client.capabilities()}
    discovered: list[MailboxFact] = []
    for attributes, _delimiter, name in client.list_folders():
      normalized_name = str(name)
      if normalized_name.casefold() in {name.casefold() for name in excluded.names}:
        continue
      rendered_attributes = {
        _text(item) or "" for item in attributes if _text(item) is not None
      }
      if "\\Noselect" in rendered_attributes:
        continue
      if {item.casefold() for item in rendered_attributes} & {
        item.casefold() for item in excluded.special_uses
      }:
        continue
      selected = client.select_folder(normalized_name, readonly=True)
      uid_validity = _number(selected[b"UIDVALIDITY"])
      mailbox_id = None
      if "OBJECTID" in capabilities:
        try:
          status = client.folder_status(normalized_name, [b"MAILBOXID"])
          mailbox_id = _normalized_token(status.get(b"MAILBOXID"))
          if mailbox_id and mailbox_id.startswith("(") and mailbox_id.endswith(")"):
            mailbox_id = mailbox_id[1:-1]
        except Exception:
          mailbox_id = None
      special_uses = tuple(sorted(rendered_attributes & _SPECIAL_USES))
      discovered.append(
        MailboxFact(
          mailbox=CanonicalMailbox(
            name=normalized_name,
            special_uses=special_uses,
            mailbox_id=mailbox_id,
          ),
          uid_validity=uid_validity,
        )
      )
    return tuple(discovered)

  async def read_ordinary_changes(
    self,
    mailbox: MailboxFact,
    checkpoint: IMAPCheckpoint | None,
    first_horizon: datetime.datetime,
  ) -> MailboxChanges:
    return await self._run(
      self._read_ordinary_changes,
      mailbox,
      checkpoint,
      first_horizon,
    )

  def _read_ordinary_changes(
    self,
    mailbox: MailboxFact,
    checkpoint: IMAPCheckpoint | None,
    first_horizon: datetime.datetime,
  ) -> MailboxChanges:
    client = self._connected()
    selected = client.select_folder(mailbox.mailbox.name, readonly=False)
    uid_validity = _number(selected[b"UIDVALIDITY"])
    highest_modseq = selected.get(b"HIGHESTMODSEQ")
    highest_modseq = _number(highest_modseq) if highest_modseq else None
    if checkpoint is None or checkpoint.uid_validity != uid_validity:
      candidates = client.search(["SINCE", first_horizon.date()])
      message_facts = self._fetch_messages(
        mailbox.mailbox.name,
        uid_validity,
        candidates,
        since=first_horizon,
      )
      selected_frontier = max(0, _number(selected.get(b"UIDNEXT", 1)) - 1)
      last_uid = max((selected_frontier, *candidates))
      return MailboxChanges(
        messages=message_facts,
        next_checkpoint=IMAPCheckpoint(
          uid_validity=uid_validity,
          last_uid=last_uid,
          highest_modseq=highest_modseq,
        ),
      )

    removed_uids: tuple[int, ...] = ()
    if self._qresync_enabled and checkpoint.highest_modseq is not None:
      selected, removed_uids = self._select_qresync(
        mailbox.mailbox.name,
        checkpoint,
      )
      highest_modseq = selected.get(b"HIGHESTMODSEQ")
      highest_modseq = _number(highest_modseq) if highest_modseq else None

    new_uids = [
      uid
      for uid in client.search(["UID", f"{checkpoint.last_uid + 1}:*"])
      if uid > checkpoint.last_uid
    ]
    messages = self._fetch_messages(
      mailbox.mailbox.name,
      uid_validity,
      new_uids,
    )
    flag_changes: list[FlagChangeFact] = []
    if checkpoint.highest_modseq is not None and highest_modseq is not None:
      try:
        changed = client.fetch(
          "1:*",
          ["FLAGS", "MODSEQ"],
          modifiers=["CHANGEDSINCE", str(checkpoint.highest_modseq)],
        )
      except Exception:
        changed = {}
      new_uid_set = set(new_uids)
      for uid, response in changed.items():
        if int(uid) in new_uid_set:
          continue
        flags = tuple(
          flag
          for value in response.get(b"FLAGS", ())
          if (flag := _text(value)) and flag.casefold() != "\\recent"
        )
        modseq = response.get(b"MODSEQ")
        if isinstance(modseq, (tuple, list)) and modseq:
          modseq = modseq[0]
        flag_changes.append(
          FlagChangeFact(
            uid=int(uid),
            uid_validity=uid_validity,
            flags=flags,
            modseq=int(modseq) if modseq else None,
          )
        )
    return MailboxChanges(
      messages=messages,
      flag_changes=tuple(flag_changes),
      removed_uids=removed_uids,
      next_checkpoint=IMAPCheckpoint(
        uid_validity=uid_validity,
        last_uid=max((checkpoint.last_uid, *new_uids)),
        highest_modseq=highest_modseq,
      ),
    )

  def _select_qresync(
    self,
    mailbox_name: str,
    checkpoint: IMAPCheckpoint,
  ) -> tuple[dict[bytes, typing.Any], tuple[int, ...]]:
    """SELECT with QRESYNC through IMAPClient's pinned low-level command seam."""
    client = self._connected()
    known_uids = f" 1:{checkpoint.last_uid}" if checkpoint.last_uid else ""
    modifier = (
      f"(QRESYNC ({checkpoint.uid_validity} {checkpoint.highest_modseq}{known_uids}))"
    ).encode("ascii")
    # IMAPClient has no public QRESYNC SELECT; keep this pinned seam inside Adapter.
    imap = client._imap
    imap.untagged_responses = {}
    imap.is_readonly = False
    response_type, response = client._raw_command(
      b"SELECT",
      [client._normalise_folder(mailbox_name), modifier],
      uid=False,
    )
    if response_type != "OK":
      imap.state = "AUTH"
    client._checkok("SELECT", response_type, response)
    imap.state = "SELECTED"
    vanished = tuple(
      uid
      for value in imap.untagged_responses.get("VANISHED", ())
      for uid in _parse_uid_set(value)
    )
    selected = client._process_select_response(imap.untagged_responses)
    return selected, vanished

  async def read_backfill(
    self,
    mailbox: MailboxFact,
    interval: MailBackfillConfig,
  ) -> tuple[MessageFact, ...]:
    return await self._run(self._read_backfill, mailbox, interval)

  def _read_backfill(
    self,
    mailbox: MailboxFact,
    interval: MailBackfillConfig,
  ) -> tuple[MessageFact, ...]:
    client = self._connected()
    selected = client.select_folder(mailbox.mailbox.name, readonly=False)
    uid_validity = _number(selected[b"UIDVALIDITY"])
    criteria: list[typing.Any] = ["SINCE", interval.since]
    if interval.before is not None:
      criteria.extend(["BEFORE", interval.before])
    return self._fetch_messages(
      mailbox.mailbox.name,
      uid_validity,
      client.search(criteria),
      since=interval.since,
      before=interval.before,
    )

  def _fetch_messages(
    self,
    mailbox_name: str,
    uid_validity: int,
    uids: typing.Iterable[int],
    *,
    since: datetime.datetime | datetime.date | None = None,
    before: datetime.datetime | datetime.date | None = None,
  ) -> tuple[MessageFact, ...]:
    client = self._connected()
    result: list[MessageFact] = []
    capabilities = {(_text(item) or "").upper() for item in client.capabilities()}
    fetch_items = ["BODY.PEEK[HEADER]", "BODYSTRUCTURE", "FLAGS", "INTERNALDATE"]
    if capabilities & {"CONDSTORE", "QRESYNC"}:
      fetch_items.append("MODSEQ")
    if "OBJECTID" in capabilities:
      fetch_items.append("EMAILID")
    for uid in sorted(set(uids)):
      fetched = client.fetch([uid], fetch_items).get(uid)
      if not fetched:
        continue
      internal_date = fetched.get(b"INTERNALDATE")
      if internal_date is not None:
        internal_date = _normalized_internal_date(internal_date)
        comparable = (
          internal_date if isinstance(since, datetime.datetime) else internal_date.date()
        )
        normalized_since = (
          _normalized_internal_date(since)
          if isinstance(since, datetime.datetime)
          else since
        )
        if normalized_since is not None and comparable < normalized_since:
          continue
        comparable = (
          internal_date if isinstance(before, datetime.datetime) else internal_date.date()
        )
        normalized_before = (
          _normalized_internal_date(before)
          if isinstance(before, datetime.datetime)
          else before
        )
        if normalized_before is not None and comparable >= normalized_before:
          continue
      header_bytes = _response_value(fetched, b"BODY[HEADER]") or b""
      message = email.message_from_bytes(header_bytes, policy=email.policy.default)
      body_structure = fetched.get(b"BODYSTRUCTURE")
      descriptors = _walk_body_structure(body_structure)
      bodies: list[BodyFact] = []
      mime_parts: list[MimePartFact] = []
      for descriptor in descriptors:
        if descriptor.is_body:
          payload_response = client.fetch(
            [uid],
            [f"BODY.PEEK[{descriptor.part_id}]"],
          ).get(uid, {})
          payload = _response_value(
            payload_response,
            f"BODY[{descriptor.part_id}]".encode(),
          )
          if not isinstance(payload, bytes):
            continue
          decoded = decode_transfer(payload, descriptor.transfer_encoding)
          try:
            rendered = decoded.decode(descriptor.charset or "utf-8", errors="replace")
          except LookupError:
            rendered = decoded.decode("utf-8", errors="replace")
          bodies.append(
            BodyFact(
              part_id=descriptor.part_id,
              media_type=typing.cast(typing.Any, descriptor.media_type),
              content=rendered,
              alternative_groups=descriptor.alternative_groups,
            )
          )
          continue
        role = "attachment" if descriptor.disposition == "attachment" else "inline"
        mime_parts.append(
          MimePartFact(
            part_id=descriptor.part_id,
            role=typing.cast(typing.Any, role),
            metadata=CanonicalMimePart(
              media_type=descriptor.media_type,
              charset=descriptor.charset,
              filename=descriptor.filename,
              content_id=descriptor.content_id,
              description=descriptor.description,
              transfer_encoding=descriptor.transfer_encoding,
              encoded_size=descriptor.encoded_size,
              content_location=descriptor.content_location,
            ),
          )
        )
      flags = tuple(
        flag
        for value in fetched.get(b"FLAGS", ())
        if (flag := _text(value)) and flag.casefold() != "\\recent"
      )
      modseq = fetched.get(b"MODSEQ")
      if isinstance(modseq, (tuple, list)) and modseq:
        modseq = modseq[0]
      message_ids = _message_ids(str(message.get("Message-ID") or ""))
      email_id = _normalized_token(fetched.get(b"EMAILID"))
      authored_at = None
      raw_date = str(message.get("Date") or "")
      if raw_date:
        try:
          authored_at = email.utils.parsedate_to_datetime(raw_date)
        except (TypeError, ValueError, OverflowError):
          authored_at = None
      result.append(
        MessageFact(
          uid=uid,
          uid_validity=uid_validity,
          internal_date=internal_date,
          root=CanonicalEmail(
            message_id=message_ids[0] if message_ids else None,
            email_id=email_id,
            subject=_decode_header(str(message.get("Subject") or "")),
            authored_at=authored_at,
          ),
          participants=_participants(message),
          mailing_list=_mailing_list(message),
          bodies=tuple(bodies),
          mime_parts=tuple(mime_parts),
          in_reply_to=_message_ids(str(message.get("In-Reply-To") or "")),
          references=_message_ids(str(message.get("References") or "")),
          flags=flags,
          modseq=int(modseq) if modseq else None,
        )
      )
    return tuple(result)

  async def mark_seen(self, mailbox_name: str, uid: int) -> None:
    await self._run(self._mark_seen, mailbox_name, uid)

  def _mark_seen(self, mailbox_name: str, uid: int) -> None:
    client = self._connected()
    client.select_folder(mailbox_name, readonly=False)
    client.add_flags([uid], [b"\\Seen"], silent=True)

  async def fetch_part(self, mailbox_name: str, uid: int, part_id: str) -> bytes:
    return await self._run(self._fetch_part, mailbox_name, uid, part_id)

  def _fetch_part(self, mailbox_name: str, uid: int, part_id: str) -> bytes:
    client = self._connected()
    client.select_folder(mailbox_name, readonly=True)
    response = client.fetch([uid], [f"BODY.PEEK[{part_id}]"]).get(uid, {})
    payload = _response_value(response, f"BODY[{part_id}]".encode())
    if not isinstance(payload, bytes):
      raise MailAdapterError(f"MIME part {part_id!r} is unavailable")
    return payload


def create_mail_adapter(
  protocol: MailProtocol,
  parameters: IMAPParameters,
) -> IMAPAdapter:
  """Construct the shallow adapter selected by a public protocol value."""
  if protocol != "imap":  # pragma: no cover - closed Literal today
    raise ValueError(f"Unsupported Mail protocol: {protocol}")
  return IMAPAdapter(parameters)
