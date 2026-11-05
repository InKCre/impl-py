"""Sql utilities."""

import typing
import sqlalchemy
from sqlalchemy.dialects.postgresql import JSONB


def find_by_json_field(column: typing.Any, field_name: str, value: str):
  """Find by JSON field value.

  :param column: sqlalchemy column

  Examples:
      >>> find_by_json_field(YourTable.column_name, "email", "example@example.com")
  """
  return sqlalchemy.cast(column, JSONB)[field_name].astext == value


def find_by_nested_json(column: typing.Any, parent: str, child: str, value: str):
  """Find by nested JSON field value.

  :param column: sqlalchemy column
  """
  return sqlalchemy.cast(column, JSONB)[(parent, child)].astext == value


def find_by_json_contains(column: typing.Any, json_obj: dict):
  """Find by JSON contains.

  :param column: sqlalchemy column
  """
  return sqlalchemy.cast(column, JSONB).contains(json_obj)
