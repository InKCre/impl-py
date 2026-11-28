"""Isolate archive fixtures and remove only Blocks inserted by the current test."""

import json
from pathlib import Path
import typing
import uuid

import pytest
import sqlalchemy
from sqlalchemy import event
from sqlalchemy.orm import Session
import sqlmodel

from inkcre_core.schemas.info_base.block import BlockModel
from tests.database import TestSession


@pytest.fixture
def native() -> dict[str, typing.Any]:
  value = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "native_archive.json").read_text()
  )
  offset = int(uuid.uuid4().hex[:15], 16) + 10**20

  def distinct(item):
    if isinstance(item, str):
      if item.isascii() and item.isdecimal():
        return str(int(item) + offset)
      if item.startswith(("https://pbs.twimg.com/", "https://video.twimg.com/")):
        return f"{item}{'&' if '?' in item else '?'}twitter_test={offset}"
    if isinstance(item, list):
      return [distinct(child) for child in item]
    if isinstance(item, dict):
      return {key: distinct(child) for key, child in item.items()}
    return item

  return distinct(value)


@pytest.fixture(autouse=True)
def tracked() -> typing.Iterator[set[int]]:
  ids: set[int] = set()

  def remember_inserts(session, _context):
    ids.update(
      row.id for row in session.new if isinstance(row, BlockModel) and row.id is not None
    )

  event.listen(Session, "after_flush", remember_inserts)
  try:
    yield ids
  finally:
    event.remove(Session, "after_flush", remember_inserts)
    if ids:
      with TestSession() as db:
        db.connection().execute(
          sqlalchemy.delete(BlockModel).where(sqlmodel.col(BlockModel.id).in_(ids))
        )
        db.commit()
