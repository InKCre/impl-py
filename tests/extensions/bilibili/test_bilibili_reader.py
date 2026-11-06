"""同一 v1 图不依赖采集浏览器即可进入现有词法投影。"""

import datetime
import json

import pydantic
import pytest

from extensions.bilibili.resolver import (
  FolderResolver,
  MembershipResolver,
  UserResolver,
  VideoResolver,
)
from extensions.bilibili.schema import (
  FOLDER_RESOLVER,
  MEMBERSHIP_RESOLVER,
  USER_RESOLVER,
  VIDEO_RESOLVER,
)
from inkcre_core.business.info_base.resolver import ResolverManager, UnknownResolverError
from inkcre_core.business.lexical_retrieval.main import LexicalRetrievalManager
from inkcre_core.schemas.info_base.block import BlockModel


def block(resolver: str, content: dict) -> BlockModel:
  now = datetime.datetime.now(datetime.UTC)
  return BlockModel(
    id=41,
    resolver=resolver,
    content=json.dumps(content, ensure_ascii=False),
    storage=None,
    created_at=now,
    updated_at=now,
  )


@pytest.mark.parametrize(
  "resolver_cls,resolver_id,content,expected",
  [
    (
      UserResolver,
      USER_RESOLVER,
      {"mid": "100", "name": "测试 UP", "avatar_url": "https://example.test/avatar.jpg"},
      ("测试 UP", "测试 UP"),
    ),
    (
      FolderResolver,
      FOLDER_RESOLVER,
      {"id": "90001", "title": "系统设计", "description": "架构资料", "is_private": True},
      ("系统设计", "系统设计\n架构资料"),
    ),
    (
      VideoResolver,
      VIDEO_RESOLVER,
      {
        "aid": "200",
        "bvid": "BV1ab411c7AB",
        "title": "数据库事务",
        "description": "讨论隔离级别与原子提交。",
        "duration_seconds": 180,
        "published_at": "2026-01-01T00:00:00Z",
        "cover_url": "https://example.test/cover.jpg",
      },
      ("数据库事务", "数据库事务\n讨论隔离级别与原子提交。"),
    ),
    (
      MembershipResolver,
      MEMBERSHIP_RESOLVER,
      {
        "availability": "unavailable",
        "page_number": 2,
        "position_in_page": 3,
        "favored_at": "2026-01-02T00:00:00Z",
        "observed_at": "2026-01-03T00:00:00Z",
      },
      ("已失效的收藏条目", "已失效的收藏条目"),
    ),
  ],
)
def test_web_records_project_without_browser(
  async_runner, resolver_cls, resolver_id, content, expected
):
  label, text = expected
  value = block(resolver_id, content)
  reader = resolver_cls(value)
  assert async_runner.run(reader.get_label()) == label
  assert async_runner.run(reader.get_text(context="lexical")) == text
  projection = async_runner.run(LexicalRetrievalManager._project(value))
  assert (projection.block, projection.label, projection.text) == (41, label, text)


@pytest.mark.parametrize(
  "resolver_id,content,expected",
  [
    (USER_RESOLVER, {"mid": "100"}, "UID 100"),
    (FOLDER_RESOLVER, {"id": "90001"}, "收藏夹 90001"),
    (VIDEO_RESOLVER, {"aid": "200", "bvid": "BV1ab411c7AB"}, "BV1ab411c7AB"),
    (VIDEO_RESOLVER, {"aid": "200"}, "av200"),
  ],
)
def test_sparse_identity_has_a_readable_fallback(
  async_runner, resolver_id, content, expected
):
  projection = async_runner.run(
    LexicalRetrievalManager._project(block(resolver_id, content))
  )
  assert projection.label == projection.text == expected


@pytest.mark.parametrize(
  "content",
  [
    {"aid": 200},
    {"aid": "2١"},
    {"aid": "200", "title": None},
    {"aid": "200", "duration_seconds": "180"},
    {"aid": "200", "published_at": "2026-01-01T00:00:00+08:00"},
    {"aid": "200", "unexpected": "cannot silently reinterpret a future record"},
  ],
)
def test_malformed_v1_records_fail_instead_of_inventing_text(async_runner, content):
  with pytest.raises(pydantic.ValidationError):
    async_runner.run(VideoResolver(block(VIDEO_RESOLVER, content)).get_text())


def test_unknown_version_is_not_guessed():
  with pytest.raises(UnknownResolverError):
    ResolverManager.get(block("extensions.bilibili.video.v2", {"aid": "200"}))
