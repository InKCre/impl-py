"""Independent info-base use cases; composed writes use GraphUnitOfWork directly."""

import typing
from typing import Optional as Opt
from inkcre_core.schemas.info_base.block import BlockForm, BlockModel, BlockID, ResolverType
from inkcre_core.schemas.info_base.relation import RelationModel, RelationID
from inkcre_core.schemas.info_base.storage import StorageID
from inkcre_core.utils.types_ import Undefined, _undefined
from inkcre_core.persistence.info_base.uow import graph_uow


class BlockService:
  @staticmethod
  async def discover_resolvers(*, limit: int, after: str | None = None) -> list[dict]:
    """Read exact Resolver values in persisted Blocks, independent of code loading."""
    async with graph_uow() as uow:
      return await uow.blocks.discover_resolvers(limit=limit, after=after)

  @staticmethod
  async def discover_blocks(
    resolver: str,
    *,
    limit: int,
    after: int | None = None,
  ) -> list[dict]:
    """Read one Resolver's Block references, never content or storage pointers."""
    async with graph_uow() as uow:
      return await uow.blocks.discover_blocks(resolver, limit=limit, after=after)

  @staticmethod
  async def get_many(block_ids: typing.Collection[BlockID]) -> tuple[BlockModel, ...]:
    async with graph_uow() as uow:
      result = await uow.blocks.get_many(block_ids)
    return result

  @staticmethod
  async def get_random_many(count: int) -> tuple[BlockModel, ...]:
    async with graph_uow() as uow:
      result = await uow.blocks.get_random_many(count)
    return result

  @staticmethod
  async def get_random() -> Opt[BlockModel]:
    async with graph_uow() as uow:
      result = await uow.blocks.get_random()
    return result

  @staticmethod
  async def get(block_id: BlockID) -> Opt[BlockModel]:
    async with graph_uow() as uow:
      result = await uow.blocks.get(block_id)
    return result

  @staticmethod
  async def edit_block(
    block_id: BlockID,
    content: Opt[str] = None,
    resolver: Opt[ResolverType] = None,
    storage: Opt[StorageID] | Undefined = _undefined,
  ) -> BlockModel:
    async with graph_uow() as uow:
      result = await uow.blocks.edit_block(block_id, content, resolver, storage)
    return result

  @staticmethod
  async def delete(block_id: BlockID) -> bool:
    async with graph_uow() as uow:
      result = await uow.blocks.delete(block_id)
    return result

  @staticmethod
  async def get_recent(
    num: int = 10, resolver: Opt[ResolverType] = None
  ) -> tuple[BlockModel, ...]:
    """获取最新的块

    按创建时间倒序排序。

    :param num: 获取的块数量
    :param resolver: 限定解析器类型, None则不限定
    """
    async with graph_uow() as uow:
      result = await uow.blocks.get_recent(num, resolver)
    return result

  @staticmethod
  async def create(form: BlockForm) -> BlockModel:
    async with graph_uow() as uow:
      result = await uow.blocks.create(form)
    return result


class RelationService:
  @staticmethod
  async def get_text(
    relation: RelationModel,
    *,
    refresh: bool = False,
  ) -> str | None:
    """Project one directed dynamic property through Block-local endpoint labels."""
    if not relation.content.strip():
      return None
    async with graph_uow() as uow:
      endpoints = {
        block.id: block
        for block in await uow.blocks.get_many((relation.from_, relation.to_))
      }
    from_block = endpoints.get(relation.from_)
    to_block = endpoints.get(relation.to_)
    if from_block is None or to_block is None:
      return None

    # Local imports avoid reversing Resolver -> RelationService ownership.
    from inkcre_core.business.info_base.resolver import ResolverManager

    subject = await ResolverManager.get(from_block).get_label(refresh=refresh)
    value = await ResolverManager.get(to_block).get_label(refresh=refresh)
    if not subject.strip() or not value.strip():
      return None
    return f"subject:\n{subject}\nproperty:\n{relation.content}\nvalue:\n{value}"

  @staticmethod
  async def get_many(
    relation_ids: typing.Collection[RelationID],
  ) -> tuple[RelationModel, ...]:
    async with graph_uow() as uow:
      result = await uow.relations.get_many(relation_ids)
    return result

  @staticmethod
  async def get_by_id(relation_id: RelationID) -> RelationModel | None:
    async with graph_uow() as uow:
      result = await uow.relations.get_by_id(relation_id)
    return result

  @staticmethod
  async def get_endpoint_page(
    block_ids: typing.Collection[BlockID],
    *,
    endpoint: typing.Literal["from", "to"],
    contents: typing.Collection[str] = (),
    cursor: RelationID | None = None,
    limit: int = 21,
  ) -> tuple[RelationModel, ...]:
    async with graph_uow() as uow:
      result = await uow.relations.get_endpoint_page(
        block_ids, endpoint=endpoint, contents=contents, cursor=cursor, limit=limit
      )
    return result

  @staticmethod
  async def get(
    block_id: BlockID,
    include_in: bool = True,
    include_out: bool = True,
    content: Opt[str] = None,
  ) -> tuple[RelationModel, ...]:
    async with graph_uow() as uow:
      result = await uow.relations.get(block_id, include_in, include_out, content)
    return result

  @staticmethod
  async def update(
    relation_id: RelationID,
    *,
    from_: BlockID | Undefined = _undefined,
    to_: BlockID | Undefined = _undefined,
    content: str | Undefined = _undefined,
  ) -> RelationModel:
    async with graph_uow() as uow:
      result = await uow.relations.update(
        relation_id, from_=from_, to_=to_, content=content
      )
    return result

  @staticmethod
  async def delete(relation_id: RelationID) -> bool:
    async with graph_uow() as uow:
      result = await uow.relations.delete(relation_id)
    return result

  @staticmethod
  async def create(from_: BlockID, to_: BlockID, content: str) -> RelationModel:
    async with graph_uow() as uow:
      result = await uow.relations.create(from_, to_, content)
    return result


async def get_entity_records(
  block_ids: typing.Collection[BlockID], relation_ids: typing.Collection[RelationID]
) -> tuple[dict[BlockID, BlockModel], dict[RelationID, RelationModel]]:
  """Read both entity kinds in one short scope before any content hydration."""
  async with graph_uow() as uow:
    blocks = await uow.blocks.get_many(block_ids)
    relations = await uow.relations.get_many(relation_ids)
  # Persisted rows always have primary keys; the shared model also permits new rows.
  return (
    {typing.cast(BlockID, block.id): block for block in blocks},
    {typing.cast(RelationID, row.id): row for row in relations},
  )
