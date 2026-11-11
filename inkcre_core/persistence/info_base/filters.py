"""SQL predicates over authoritative Block fields, before owner ranking/limits."""

import sqlmodel

from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.query import BlockQueryFilters


def block_predicates(filters: BlockQueryFilters | None) -> list:
  conditions = []
  if filters is not None:
    if filters.resolver is not None:
      conditions.append(sqlmodel.col(BlockModel.resolver) == filters.resolver.eq)
    if filters.storage is not None:
      conditions.append(sqlmodel.col(BlockModel.storage) == filters.storage.eq)
  return conditions
