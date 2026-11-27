"""Attachment graph ownership and application commands."""

import datetime


from app.business.info_base.resolver import ResolverManager
from app.business.info_base.storage import StorageManager, WritableStorage
from app.business.info_base.storage.postgresql import StorageBlobNotFoundError
from app.persistence.info_base.uow import GraphUnitOfWork, graph_uow
from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.info_base.relation import RelationModel

from .graph import (
  ATTACHMENT_RELATION_PREFIX,
  ATTACHMENT_RESOLVER,
  CONTENT_RELATION,
  MemoGraph,
)
from .schema import CanonicalAttachment, SolvedAttachment


DATABASE_BINARY_STORAGE_ID = -4


class AttachmentNotFoundError(LookupError):
  pass


class AttachmentOwnershipError(ValueError):
  pass


class AttachmentGraph:
  @classmethod
  async def get_block(
    cls,
    attachment_id: int,
    uow: GraphUnitOfWork,
    *,
    lock: bool = False,
  ) -> BlockModel | None:
    block = await uow.blocks.get(attachment_id, lock=lock)
    if block is None or block.resolver != ATTACHMENT_RESOLVER:
      return None
    return block

  @classmethod
  async def owner_relation(
    cls,
    attachment_id: int,
    uow: GraphUnitOfWork,
  ) -> RelationModel | None:
    relations = tuple(
      relation
      for relation in await uow.relations.get(
        attachment_id, include_in=True, include_out=False
      )
      if relation.content.startswith(ATTACHMENT_RELATION_PREFIX)
    )
    if len(relations) > 1:
      raise AttachmentOwnershipError(
        f"Attachment attachments/{attachment_id} has multiple memo owners"
      )
    return relations[0] if relations else None

  @classmethod
  async def create(
    cls,
    *,
    filename: str,
    media_type: str,
    content: bytes,
    created_at: datetime.datetime,
    uow: GraphUnitOfWork,
  ) -> BlockModel:
    record = await uow.storage.get(DATABASE_BINARY_STORAGE_ID)
    if record is None:
      raise LookupError("PostgreSQL binary storage is not registered")
    storage = StorageManager.from_record(record)
    if not isinstance(storage, WritableStorage):
      raise TypeError("Configured PostgreSQL binary storage is not writable")
    block_pointer = await storage.create_content(content, uow.storage)
    canonical = CanonicalAttachment(
      filename=filename,
      media_type=media_type,
      size=len(content),
      created_at=created_at,
    )
    metadata_block = await uow.blocks.create(
      BlockForm(resolver=ATTACHMENT_RESOLVER, content=canonical.to_block_content())
    )
    semantic_resolver = ResolverManager.match_media_type(media_type) or "core.file.v1"
    content_block = await uow.blocks.create(
      BlockForm(
        resolver=semantic_resolver,
        storage=DATABASE_BINARY_STORAGE_ID,
        content=block_pointer,
      )
    )
    if metadata_block.id is None or content_block.id is None:
      raise RuntimeError("Persisted attachment graph contains an unassigned block ID")
    await uow.relations.create(metadata_block.id, content_block.id, CONTENT_RELATION)
    return metadata_block

  @classmethod
  async def content_block(
    cls,
    attachment_id: int,
    uow: GraphUnitOfWork,
  ) -> BlockModel:
    relations = tuple(
      relation
      for relation in await uow.relations.get(
        attachment_id, include_in=False, include_out=True, content=CONTENT_RELATION
      )
      if relation.from_ == attachment_id
    )
    if len(relations) != 1:
      raise AttachmentOwnershipError(
        f"Attachment attachments/{attachment_id} must have exactly one content relation"
      )
    block = await uow.blocks.get(relations[0].to_)
    if block is None:
      raise AttachmentNotFoundError(
        f"Attachment content block {relations[0].to_} not found"
      )
    ResolverManager.get(block)
    return block

  @classmethod
  async def current_attachment_relations(
    cls,
    memo_id: int,
    uow: GraphUnitOfWork,
  ) -> dict[int, RelationModel]:
    positions: dict[int, RelationModel] = {}
    for relation in await uow.relations.get(memo_id, include_in=False, include_out=True):
      if not relation.content.startswith(ATTACHMENT_RELATION_PREFIX):
        continue
      raw_position = relation.content.removeprefix(ATTACHMENT_RELATION_PREFIX)
      if not raw_position.isdigit():
        raise ValueError(f"Invalid attachment relation: {relation.content}")
      position = int(raw_position)
      if position in positions:
        raise ValueError(f"Duplicate attachment position: {position}")
      positions[position] = relation
    if positions and sorted(positions) != list(range(len(positions))):
      raise ValueError("Attachment positions must be contiguous and zero-based")
    return positions

  @classmethod
  async def set_memo_attachments(
    cls,
    memo_id: int,
    attachment_ids: tuple[int, ...],
    uow: GraphUnitOfWork,
  ) -> None:
    if len(set(attachment_ids)) != len(attachment_ids):
      raise AttachmentOwnershipError("Attachment list contains duplicate identities")
    if await MemoGraph.get_root(memo_id, uow, lock=True) is None:
      raise AttachmentNotFoundError(f"Memo memos/{memo_id} not found")

    # Serialize ownership checks in stable order across concurrent memo writes.
    for attachment_id in sorted(attachment_ids):
      if await cls.get_block(attachment_id, uow, lock=True) is None:
        raise AttachmentNotFoundError(f"Attachment attachments/{attachment_id} not found")
      owner = await cls.owner_relation(attachment_id, uow)
      if owner is not None and owner.from_ != memo_id:
        raise AttachmentOwnershipError(
          f"Attachment attachments/{attachment_id} already has an owner"
        )

    current = await cls.current_attachment_relations(memo_id, uow)
    current_ids = {relation.to_ for relation in current.values()}
    requested_ids = set(attachment_ids)

    for position, attachment_id in enumerate(attachment_ids):
      relation = current.get(position)
      if relation is None:
        await uow.relations.create(
          memo_id, attachment_id, f"{ATTACHMENT_RELATION_PREFIX}{position}"
        )
      elif relation.to_ != attachment_id:
        if relation.id is None:
          raise RuntimeError("Persisted attachment relation has no ID")
        await uow.relations.update(relation.id, to_=attachment_id)

    for position, relation in current.items():
      if position >= len(attachment_ids):
        if relation.id is None:
          raise RuntimeError("Persisted attachment relation has no ID")
        await uow.relations.delete(relation.id)

    for removed_id in current_ids - requested_ids:
      await cls.delete_component(removed_id, uow)

  @classmethod
  async def delete_component(
    cls,
    attachment_id: int,
    uow: GraphUnitOfWork,
  ) -> bool:
    block = await cls.get_block(attachment_id, uow, lock=True)
    if block is None:
      return False
    content_block = await cls.content_block(attachment_id, uow)
    if content_block.id is None:
      raise RuntimeError("Persisted semantic content block has no ID")
    other_content_owners = tuple(
      relation
      for relation in await uow.relations.get(
        content_block.id, include_in=True, include_out=False, content=CONTENT_RELATION
      )
      if relation.to_ == content_block.id and relation.from_ != attachment_id
    )
    deleted = await uow.blocks.delete(attachment_id)
    if other_content_owners:
      return deleted

    if content_block.storage is None:
      raise TypeError("Attachment semantic content must use writable storage")
    record = await uow.storage.get(content_block.storage)
    if record is None:
      raise LookupError("Attachment storage is not registered")
    storage = StorageManager.from_record(record)
    if not isinstance(storage, WritableStorage):
      raise TypeError("Attachment semantic content storage is not writable")
    await storage.delete_content(content_block.content, uow.storage)
    await uow.blocks.delete(content_block.id)
    return deleted


class AttachmentApplicationService:
  @classmethod
  async def create(
    cls,
    *,
    filename: str,
    media_type: str,
    content: bytes,
    memo_id: int | None = None,
    now: datetime.datetime | None = None,
  ) -> SolvedAttachment:
    created_at = now or datetime.datetime.now(datetime.UTC)
    async with graph_uow() as uow:
      # Keep the read-modify-append operation under the memo's existing write lock.
      if memo_id is not None and await MemoGraph.get_root(memo_id, uow, lock=True) is None:
        raise AttachmentNotFoundError(f"Memo memos/{memo_id} not found")
      block = await AttachmentGraph.create(
        filename=filename,
        media_type=media_type,
        content=content,
        created_at=created_at,
        uow=uow,
      )
      if block.id is None:
        raise RuntimeError("Persisted attachment block has no ID")
      if memo_id is not None:
        current = await AttachmentGraph.current_attachment_relations(memo_id, uow)
        await AttachmentGraph.set_memo_attachments(
          memo_id,
          tuple(relation.to_ for _, relation in sorted(current.items())) + (block.id,),
          uow,
        )

    return await cls._solve(block)

  @classmethod
  async def list(cls) -> tuple[SolvedAttachment, ...]:
    async with graph_uow() as uow:
      blocks = sorted(
        await uow.blocks.get_by_resolvers((ATTACHMENT_RESOLVER,)),
        key=lambda block: (block.created_at, block.id or 0),
        reverse=True,
      )
    return tuple([await cls._solve(block) for block in blocks])

  @classmethod
  async def download(cls, attachment_id: int, filename: str) -> tuple[str, bytes]:
    async with graph_uow() as uow:
      block = await AttachmentGraph.get_block(attachment_id, uow)
    if block is None:
      raise AttachmentNotFoundError(f"Attachment attachments/{attachment_id} not found")
    solved = await cls._solve(block)
    if solved.canonical.filename != filename:
      raise AttachmentNotFoundError(
        f"Attachment filename does not match attachments/{attachment_id}"
      )
    try:
      async with graph_uow() as uow:
        content_block = await AttachmentGraph.content_block(
          attachment_id,
          uow,
        )
      content = await ResolverManager.get(content_block).get_raw_content()
    except StorageBlobNotFoundError as error:
      raise AttachmentNotFoundError(str(error)) from error
    if not isinstance(content, bytes):
      raise TypeError("Attachment storage returned non-binary content")
    return solved.canonical.media_type, content

  @classmethod
  async def delete(cls, attachment_id: int) -> None:
    async with graph_uow() as uow:
      if not await AttachmentGraph.delete_component(attachment_id, uow):
        raise AttachmentNotFoundError(f"Attachment attachments/{attachment_id} not found")

  @classmethod
  async def _solve(cls, block: BlockModel) -> SolvedAttachment:
    solved = await ResolverManager.get(block).get_solved_content()
    if not isinstance(solved, SolvedAttachment):
      raise TypeError("Attachment resolver returned an unexpected solved value")
    return solved
