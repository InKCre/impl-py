"""Graph persistence within a caller-owned asynchronous transaction."""

from collections.abc import Iterable
import random
import typing
from typing import Optional as Opt
import sqlalchemy
import sqlmodel
from utils.types_ import Undefined, _undefined
from app.schemas.info_base.block import BlockID, ResolverType
from app.schemas.info_base.relation import RelationID
from app.schemas.info_base.storage import StorageID

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.info_base.block import BlockForm, BlockModel
from app.schemas.info_base.relation import RelationCreateForm, RelationModel


class BlockRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def create_many(self, forms: Iterable[BlockForm]) -> tuple[BlockModel, ...]:
    """Insert producer fields and load database identities in one flush."""
    blocks = tuple(
      BlockModel.model_validate(form.model_dump(include=set(BlockForm.model_fields)))
      for form in forms
    )
    if blocks:
      self._session.add_all(blocks)
      await self._session.flush()
    return blocks

  async def get_many(self, block_ids: typing.Collection[BlockID]) -> tuple[BlockModel, ...]:
    """Return the existing Blocks from a bounded identity set."""
    if not block_ids:
      return ()
    return tuple(
      (
        await self._session.scalars(
          sqlmodel.select(BlockModel).where(
            sqlmodel.col(BlockModel.id).in_(tuple(block_ids))
          )
        )
      ).all()
    )

  async def get_by_resolvers(
    self, resolvers: typing.Collection[str]
  ) -> tuple[BlockModel, ...]:
    if not resolvers:
      return ()
    return tuple(
      await self._session.scalars(
        sqlmodel.select(BlockModel).where(
          sqlmodel.col(BlockModel.resolver).in_(tuple(resolvers))
        )
      )
    )

  async def save_many(self, blocks: Iterable[BlockModel]) -> None:
    self._session.add_all(blocks)
    await self._session.flush()

  async def get_random_many(self, count: int) -> tuple[BlockModel, ...]:
    """Return up to count distinct random Blocks."""
    if count <= 0:
      return ()
    return tuple(
      (
        await self._session.scalars(
          sqlmodel.select(BlockModel).order_by(sqlmodel.func.random()).limit(count)
        )
      ).all()
    )

  async def get_random(self) -> Opt[BlockModel]:
    """Return one existing Block without transferring all identities."""
    block_id_column = sqlmodel.col(BlockModel.id)
    count = (
      await self._session.scalars(sqlmodel.select(sqlmodel.func.count(block_id_column)))
    ).one()
    if count == 0:
      return None
    offset = random.SystemRandom().randrange(count)
    return (
      await self._session.scalars(
        sqlmodel.select(BlockModel).order_by(block_id_column).offset(offset).limit(1)
      )
    ).one()

  async def get(self, block_id: BlockID, *, lock: bool = False) -> Opt[BlockModel]:
    statement = sqlmodel.select(BlockModel).where(BlockModel.id == block_id)
    if lock:
      statement = statement.with_for_update()
    return (await self._session.scalars(statement)).one_or_none()

  async def save(self, block: BlockModel) -> None:
    self._session.add(block)
    await self._session.flush()

  async def edit_block(
    self,
    block_id: BlockID,
    content: Opt[str] = None,
    resolver: Opt[ResolverType] = None,
    storage: Opt[StorageID] | Undefined = _undefined,
  ) -> BlockModel:
    """编辑块"""
    block = await self.get(block_id)
    if block is None:
      raise ValueError("Block not found")
    if content is not None:
      block.content = content
    if resolver is not None:
      block.resolver = resolver
    if storage is not _undefined:
      block.storage = typing.cast(StorageID | None, storage)
    self._session.add(block)
    await self._session.flush()
    await self._session.refresh(block)
    return block

  async def delete(self, block_id: BlockID) -> bool:
    """Delete a block, within the surrounding transaction."""
    block = await self.get(block_id)
    if block is None:
      return False
    await self._session.delete(block)
    await self._session.flush()
    return True

  async def get_recent(
    self, num: int = 10, resolver: Opt[ResolverType] = None
  ) -> tuple[BlockModel, ...]:
    statement = (
      sqlmodel.select(BlockModel).order_by(sqlmodel.desc(BlockModel.created_at)).limit(num)
    )
    if resolver:
      statement = statement.where(BlockModel.resolver == resolver)
    return tuple((await self._session.scalars(statement)).all())

  async def create(self, form: BlockForm) -> BlockModel:
    return (await self.create_many((form,)))[0]

  async def find_content(self, resolver: str, content: str) -> BlockModel | None:
    return (
      await self._session.scalars(
        sqlmodel.select(BlockModel).where(
          BlockModel.resolver == resolver,
          BlockModel.content == content,
        )
      )
    ).one_or_none()

  async def find_json_field(
    self,
    resolver: str,
    field: str,
    value: str,
    *,
    block_ids: typing.Collection[BlockID] | None = None,
  ) -> tuple[BlockModel, ...]:
    from utils.sql import find_by_json_field

    statement = sqlmodel.select(BlockModel).where(
      BlockModel.resolver == resolver,
      find_by_json_field(BlockModel.content, field, value),
    )
    if block_ids is not None:
      statement = statement.where(sqlmodel.col(BlockModel.id).in_(tuple(block_ids)))
    return tuple(await self._session.scalars(statement))

  async def find_json_field_many(
    self,
    resolvers: typing.Collection[str],
    field: str,
    values: typing.Collection[str],
  ) -> tuple[BlockModel, ...]:
    """Find inline JSON records in a bounded native-identity set."""
    from sqlalchemy.dialects.postgresql import JSONB

    if not resolvers or not values:
      return ()
    return tuple(
      await self._session.scalars(
        sqlmodel.select(BlockModel)
        .where(
          sqlmodel.col(BlockModel.resolver).in_(tuple(resolvers)),
          sqlalchemy.case(
            (
              sqlalchemy.func.pg_input_is_valid(BlockModel.content, "jsonb"),
              sqlalchemy.cast(BlockModel.content, JSONB)[field].astext,
            )
          ).in_(tuple(values)),
          sqlmodel.col(BlockModel.storage).is_(None),
        )
        .order_by(sqlmodel.col(BlockModel.id))
      )
    )

  async def acquire_transaction_lock(self, namespace: str) -> None:
    """Serialize an owning use case until its surrounding transaction ends."""
    await self._session.execute(
      sqlalchemy.text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"),
      {"name": namespace},
    )

  async def get_related(
    self, block_id: BlockID, *, content: str, outgoing: bool = True
  ) -> BlockModel | None:
    endpoint = RelationModel.from_ if outgoing else RelationModel.to_
    other = RelationModel.to_ if outgoing else RelationModel.from_
    statement = (
      sqlmodel.select(BlockModel)
      .join(RelationModel, sqlmodel.col(BlockModel.id) == other)
      .where(
        endpoint == block_id,
        RelationModel.content == content,
      )
      .limit(1)
    )
    return (await self._session.scalars(statement)).first()

  async def select_ids(
    self,
    limit: int,
    *,
    exclude_resolvers: typing.Collection[str],
    random_order: bool = False,
  ) -> tuple[BlockID, ...]:
    statement = sqlmodel.select(BlockModel.id).where(
      sqlmodel.col(BlockModel.resolver).not_in(tuple(exclude_resolvers))
    )
    statement = (
      statement.order_by(sqlalchemy.func.random())
      if random_order
      else statement.order_by(
        sqlmodel.desc(BlockModel.updated_at), sqlmodel.desc(BlockModel.id)
      )
    )
    return tuple(
      typing.cast(BlockID, value)
      for value in await self._session.scalars(statement.limit(limit))
    )

  async def matching_content(self, resolver: str, content: str) -> tuple[BlockModel, ...]:
    return tuple(
      await self._session.scalars(
        sqlmodel.select(BlockModel).where(
          BlockModel.resolver == resolver, BlockModel.content == content
        )
      )
    )

  async def matching_content_many(
    self, resolver: str, contents: typing.Collection[str]
  ) -> tuple[BlockModel, ...]:
    """Find a bounded set of exact content identities in one database round trip."""
    if not contents:
      return ()
    return tuple(
      await self._session.scalars(
        sqlmodel.select(BlockModel)
        .where(
          BlockModel.resolver == resolver,
          sqlmodel.col(BlockModel.content).in_(tuple(contents)),
        )
        .order_by(sqlmodel.col(BlockModel.id))
      )
    )

  async def without_outgoing_relation(
    self, resolvers: typing.Collection[str], content: str, *, limit: int
  ) -> tuple[BlockModel, ...]:
    exists = sqlalchemy.exists(
      sqlmodel.select(RelationModel.id).where(
        RelationModel.from_ == BlockModel.id, RelationModel.content == content
      )
    )
    return tuple(
      await self._session.scalars(
        sqlmodel.select(BlockModel)
        .where(sqlmodel.col(BlockModel.resolver).in_(tuple(resolvers)), ~exists)
        .order_by(sqlmodel.col(BlockModel.id))
        .limit(limit)
      )
    )


class RelationRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def create_many(
    self, forms: Iterable[RelationCreateForm]
  ) -> tuple[RelationModel, ...]:
    """Insert directed relations without committing the surrounding operation."""
    relations = tuple(RelationModel.model_validate(form) for form in forms)
    if relations:
      self._session.add_all(relations)
      await self._session.flush()
    return relations

  async def get_many(
    self, relation_ids: typing.Collection[RelationID]
  ) -> tuple[RelationModel, ...]:
    """Return the existing Relations from a bounded identity set."""
    if not relation_ids:
      return ()
    return tuple(
      (
        await self._session.scalars(
          sqlmodel.select(RelationModel).where(
            sqlmodel.col(RelationModel.id).in_(tuple(relation_ids))
          )
        )
      ).all()
    )

  async def get_for_endpoints(
    self, block_ids: typing.Collection[BlockID], *, contents: typing.Collection[str]
  ) -> tuple[RelationModel, ...]:
    if not block_ids:
      return ()
    return tuple(
      await self._session.scalars(
        sqlmodel.select(RelationModel).where(
          sqlmodel.col(RelationModel.content).in_(tuple(contents)),
          sqlalchemy.or_(
            sqlmodel.col(RelationModel.from_).in_(tuple(block_ids)),
            sqlmodel.col(RelationModel.to_).in_(tuple(block_ids)),
          ),
        )
      )
    )

  async def delete_many(self, relation_ids: typing.Collection[RelationID]) -> None:
    if relation_ids:
      await self._session.execute(
        sqlalchemy.delete(RelationModel).where(
          sqlmodel.col(RelationModel.id).in_(tuple(relation_ids))
        )
      )

  async def get_outgoing_many(
    self,
    block_ids: typing.Collection[BlockID],
    *,
    to_ids: typing.Collection[BlockID] | None = None,
    content: str | None = None,
  ) -> tuple[RelationModel, ...]:
    if not block_ids:
      return ()
    statement = sqlmodel.select(RelationModel).where(
      sqlmodel.col(RelationModel.from_).in_(tuple(block_ids))
    )
    if to_ids is not None:
      statement = statement.where(sqlmodel.col(RelationModel.to_).in_(tuple(to_ids)))
    if content is not None:
      statement = statement.where(RelationModel.content == content)
    return tuple(await self._session.scalars(statement))

  async def save(self, relation: RelationModel) -> None:
    self._session.add(relation)
    await self._session.flush()

  async def remove(self, relation: RelationModel) -> None:
    await self._session.delete(relation)

  async def get_by_id(self, relation_id: RelationID) -> RelationModel | None:
    return await self._session.get(RelationModel, relation_id)

  async def get_endpoint_page(
    self,
    block_ids: typing.Collection[BlockID],
    *,
    endpoint: typing.Literal["from", "to"],
    contents: typing.Collection[str] = (),
    cursor: RelationID | None = None,
    limit: int = 21,
  ) -> tuple[RelationModel, ...]:
    """Read a bounded Relation page for one persisted endpoint direction."""
    if not block_ids or limit <= 0:
      return ()
    endpoint_column = RelationModel.from_ if endpoint == "from" else RelationModel.to_
    statement = sqlmodel.select(RelationModel).where(
      sqlmodel.col(endpoint_column).in_(tuple(block_ids))
    )
    if contents:
      statement = statement.where(sqlmodel.col(RelationModel.content).in_(tuple(contents)))
    relation_id_column = sqlmodel.col(RelationModel.id)
    if cursor is not None:
      statement = statement.where(relation_id_column < cursor)
    statement = statement.order_by(sqlmodel.desc(relation_id_column)).limit(limit)
    return tuple((await self._session.scalars(statement)).all())

  async def fetchsert(self, relation: RelationModel) -> RelationModel:
    """Create if not exists, else return the existing one.

    Will NOT commit the session.
    """
    existing = (
      await self._session.scalars(
        sqlmodel.select(RelationModel).where(
          RelationModel.content == relation.content,
          RelationModel.from_ == relation.from_,
          RelationModel.to_ == relation.to_,
        )
      )
    ).one_or_none()
    if existing is not None:
      return existing
    self._session.add(relation)
    await self._session.flush()
    return relation

  async def get(
    self,
    block_id: BlockID,
    include_in: bool = True,
    include_out: bool = True,
    content: Opt[str] = None,
  ) -> tuple[RelationModel, ...]:
    """Get relations from/to a block

    :param include_in: Include the relations where the block is the target
    :param include_out: Include the relations where the block is the source
    :param content: If specified, filter relations by content (eq)
    """
    if not include_in and (not include_out):
      return ()
    directions: list[typing.Any] = []
    if include_in:
      directions.append(RelationModel.to_ == block_id)
    if include_out:
      directions.append(RelationModel.from_ == block_id)
    statement = sqlmodel.select(RelationModel).where(sqlalchemy.or_(*directions))
    if content is not None:
      statement = statement.where(RelationModel.content == content)
    res = (await self._session.scalars(statement)).all()
    return tuple(res)

  async def update(
    self,
    relation_id: RelationID,
    *,
    from_: BlockID | Undefined = _undefined,
    to_: BlockID | Undefined = _undefined,
    content: str | Undefined = _undefined,
  ) -> RelationModel:
    """Update selected relation facts in the surrounding transaction."""
    relation = (
      await self._session.scalars(
        sqlmodel.select(RelationModel).where(RelationModel.id == relation_id)
      )
    ).one_or_none()
    if relation is None:
      raise ValueError("Relation not found")
    if from_ is not _undefined:
      relation.from_ = typing.cast(BlockID, from_)
    if to_ is not _undefined:
      relation.to_ = typing.cast(BlockID, to_)
    if content is not _undefined:
      relation.content = typing.cast(str, content)
    self._session.add(relation)
    await self._session.flush()
    await self._session.refresh(relation)
    return relation

  async def delete(self, relation_id: RelationID) -> bool:
    """Delete a relation, within the surrounding transaction."""
    relation = (
      await self._session.scalars(
        sqlmodel.select(RelationModel).where(RelationModel.id == relation_id)
      )
    ).one_or_none()
    if relation is None:
      return False
    await self._session.delete(relation)
    await self._session.flush()
    return True

  async def create(self, from_: BlockID, to_: BlockID, content: str) -> RelationModel:
    return (
      await self.create_many((RelationCreateForm(from_=from_, to_=to_, content=content),))
    )[0]

  async def random_incoming_sources(
    self, block_id: BlockID, content: str, limit: int
  ) -> tuple[BlockID, ...]:
    return tuple(
      await self._session.scalars(
        sqlmodel.select(RelationModel.from_)
        .where(RelationModel.to_ == block_id, RelationModel.content == content)
        .order_by(sqlalchemy.func.random())
        .limit(limit)
      )
    )

  async def recent(
    self, limit: int, *, contents: typing.Collection[str] = ()
  ) -> tuple[RelationModel, ...]:
    statement = (
      sqlmodel.select(RelationModel)
      .order_by(sqlmodel.desc(RelationModel.updated_at), sqlmodel.desc(RelationModel.id))
      .limit(limit)
    )
    if contents:
      statement = statement.where(sqlmodel.col(RelationModel.content).in_(tuple(contents)))
    return tuple(await self._session.scalars(statement))
