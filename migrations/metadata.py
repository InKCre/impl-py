"""Explicit Alembic metadata registration."""

from sqlalchemy import MetaData

from inkcre_core.database_contract import PROTOCOL_SCHEMA
from inkcre_core.schemas import Base
from inkcre_core.libs.obsrv.log_record import LogModel


_qualified_log_table = f"{PROTOCOL_SCHEMA}.{LogModel.__tablename__}"
if _qualified_log_table not in Base.metadata.tables:
  LogModel.__table__.to_metadata(Base.metadata)  # type: ignore[attr-defined]


def get_target_metadata() -> MetaData:
  """Return all application tables expected to participate in migrations."""
  if _qualified_log_table not in Base.metadata.tables:
    raise RuntimeError(
      f"Migration metadata is missing required table: {LogModel.__tablename__}"
    )
  return Base.metadata


def include_protocol_object(
  object_,
  name: str | None,
  type_: str,
  reflected: bool,
  compare_to,
) -> bool:
  """Restrict Alembic comparison to the admitted protocol schema."""
  if not reflected:
    return True
  if type_ == "table":
    return object_.schema == PROTOCOL_SCHEMA
  table = getattr(object_, "table", None)
  if table is not None:
    return table.schema == PROTOCOL_SCHEMA
  return True
