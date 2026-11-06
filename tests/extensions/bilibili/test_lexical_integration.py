"""在明确选择的开发数据库验证既有词法维护与检索，无源站访问。"""

import os
from uuid import uuid4

import pytest

from extensions.bilibili.resolver import VideoResolver
from extensions.bilibili.schema import Video
from inkcre_core.business.info_base.services import BlockService
from inkcre_core.business.lexical_retrieval import LexicalRetrievalManager
from inkcre_core.schemas.info_base.block import BlockForm
from inkcre_core.schemas.lexical_retrieval import LexicalMaintenanceOptions


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected disposable PostgreSQL runtime",
)


def test_existing_maintenance_indexes_saved_bilibili_text(async_runner):
  clue = f"bilibili-reader-{uuid4().hex}"
  content = Video(
    aid="901",
    title="事务边界研究",
    description=f"这段已保存的简介包含唯一线索 {clue}。",
  )
  created = async_runner.run(
    BlockService.create(
      BlockForm(
        resolver=VideoResolver.__rsotype__,
        content=content.model_dump_json(exclude_none=True),
      )
    )
  )
  assert created.id is not None
  try:
    report = async_runner.run(
      LexicalRetrievalManager.maintain(
        LexicalMaintenanceOptions(max_records=100_000, scan_page_size=100)
      )
    )
    assert report.indexed >= 1
    result = async_runner.run(LexicalRetrievalManager.retrieve_local(clue))
    match = next(value for value in result.matches if value.block.id == created.id)
    assert match.label == content.title
    assert clue in match.excerpt
    assert match.evidence == "text_substring"
  finally:
    async_runner.run(BlockService.delete(created.id))
