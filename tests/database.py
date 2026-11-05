"""Synchronous test-data setup/readback; never imported by application runtime.

These helpers insert raw rows, including deliberately invalid domain fixtures. Business
behavior must be exercised through the production async services, not these helpers.
"""

from contextlib import contextmanager
from collections.abc import Iterable, Generator

import sqlalchemy
import sqlmodel
from sqlalchemy.pool import NullPool

from app.settings import settings
from inkcre_core.schemas.info_base.block import BlockForm, BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel


TEST_ENGINE = sqlmodel.create_engine(settings.database_url, poolclass=NullPool)


def TestSession() -> sqlmodel.Session:
  return sqlmodel.Session(TEST_ENGINE, expire_on_commit=False)


@contextmanager
def _fixture_transaction(
  session: sqlmodel.Session | None,
) -> Generator[sqlmodel.Session, None, None]:
  if session is not None:
    yield session
  else:
    with TestSession() as owned:
      with owned.begin():
        yield owned


def seed_blocks(
  forms: Iterable[BlockForm], session: sqlmodel.Session
) -> tuple[BlockModel, ...]:
  rows = tuple(
    BlockModel.model_validate(form.model_dump(include=set(BlockForm.model_fields)))
    for form in forms
  )
  session.add_all(rows)
  session.flush()
  return rows


def seed_block(form: BlockForm, session: sqlmodel.Session | None = None) -> BlockModel:
  with _fixture_transaction(session) as db:
    return seed_blocks((form,), db)[0]


def read_block(block_id: int, session: sqlmodel.Session | None = None) -> BlockModel | None:
  with _fixture_transaction(session) as db:
    return db.get(BlockModel, block_id)


def seed_relation(
  from_: int, to_: int, content: str, db_session: sqlmodel.Session | None = None
) -> RelationModel:
  with _fixture_transaction(db_session) as db:
    row = RelationModel(from_=from_, to_=to_, content=content)
    db.add(row)
    db.flush()
    return row


def read_relations(
  block_id: int,
  include_in: bool = True,
  include_out: bool = True,
  content: str | None = None,
  db_session: sqlmodel.Session | None = None,
) -> tuple[RelationModel, ...]:
  directions = []
  if include_in:
    directions.append(sqlmodel.col(RelationModel.to_) == block_id)
  if include_out:
    directions.append(sqlmodel.col(RelationModel.from_) == block_id)
  if not directions:
    return ()
  statement = sqlmodel.select(RelationModel).where(sqlalchemy.or_(*directions))
  if content is not None:
    statement = statement.where(RelationModel.content == content)
  with _fixture_transaction(db_session) as db:
    return tuple(db.exec(statement).all())
