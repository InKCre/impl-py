"""读取共享收藏资料；词法索引归现有 LexicalRetrievalManager 维护。"""

from __future__ import annotations

import typing

from inkcre_core.business.info_base.resolver import Resolver, TextProjectionContext

from .schema import (
  FOLDER_RESOLVER,
  MEMBERSHIP_RESOLVER,
  USER_RESOLVER,
  VIDEO_RESOLVER,
  BilibiliRecord,
  Folder,
  Membership,
  User,
  Video,
  record_title,
)


RecordT = typing.TypeVar("RecordT", bound=BilibiliRecord)


class _Reader(typing.Generic[RecordT]):
  content_model: type[RecordT]

  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> RecordT:
    del materialize_missing
    resolver = typing.cast(Resolver[RecordT, str], self)
    return self.content_model.model_validate_json(
      await resolver.get_raw_content(refresh=refresh)
    )

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del context, materialize_missing
    resolver = typing.cast(Resolver[RecordT, str], self)
    value = await resolver.get_solved_content(refresh=refresh, materialize_missing=False)
    description = value.description if isinstance(value, (Folder, Video)) else None
    return "\n".join(part for part in (record_title(value), description) if part)

  async def get_label(self, *, refresh: bool = False) -> str:
    resolver = typing.cast(Resolver[RecordT, str], self)
    return record_title(
      await resolver.get_solved_content(refresh=refresh, materialize_missing=False)
    )


class UserResolver(_Reader[User], Resolver[User, str], rso_type=USER_RESOLVER):
  content_model = User


class FolderResolver(_Reader[Folder], Resolver[Folder, str], rso_type=FOLDER_RESOLVER):
  content_model = Folder


class VideoResolver(_Reader[Video], Resolver[Video, str], rso_type=VIDEO_RESOLVER):
  content_model = Video


class MembershipResolver(
  _Reader[Membership], Resolver[Membership, str], rso_type=MEMBERSHIP_RESOLVER
):
  content_model = Membership


__all__ = ["FolderResolver", "MembershipResolver", "UserResolver", "VideoResolver"]
