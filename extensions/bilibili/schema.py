"""B 站 v1 持久图记录，与 MF Distribution 共用语义。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
  AnyUrl,
  AwareDatetime,
  BaseModel,
  ConfigDict,
  Field,
  StrictBool,
  StrictInt,
  field_validator,
  model_validator,
)


USER_RESOLVER = "extensions.bilibili.user.v1"
FOLDER_RESOLVER = "extensions.bilibili.folder.v1"
VIDEO_RESOLVER = "extensions.bilibili.video.v1"
MEMBERSHIP_RESOLVER = "extensions.bilibili.membership.v1"

NativeID = Annotated[str, Field(strict=True, pattern=r"^[1-9][0-9]*$")]
NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
UTCInstant = Annotated[AwareDatetime, Field(strict=False)]


class Record(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

  @model_validator(mode="before")
  @classmethod
  def sparse_not_null(cls, value: object) -> object:
    # The MF contract uses omitted fields for unknown facts, never authored nulls.
    if isinstance(value, dict) and any(item is None for item in value.values()):
      raise ValueError("未知字段应省略，不能保存 null")
    return value


class User(Record):
  mid: NativeID
  name: str | None = None
  avatar_url: AnyUrl | None = None


class Folder(Record):
  id: NativeID
  title: str | None = None
  description: str | None = None
  is_private: StrictBool | None = None
  reported_count: NonnegativeInt | None = None


class Video(Record):
  aid: NativeID
  bvid: Annotated[str, Field(pattern=r"^BV[0-9A-Za-z]{10}$")] | None = None
  title: str | None = None
  description: str | None = None
  duration_seconds: NonnegativeInt | None = None
  published_at: UTCInstant | None = None
  cover_url: AnyUrl | None = None

  @field_validator("published_at", mode="before")
  @classmethod
  def utc_string(cls, value: object) -> object:
    if not isinstance(value, str) or not value.endswith("Z"):
      raise ValueError("来源时间必须是 UTC ISO 字符串")
    return value


class Membership(Record):
  availability: Literal["available", "unavailable", "unknown"]
  page_number: PositiveInt | None = None
  position_in_page: NonnegativeInt | None = None
  favored_at: UTCInstant | None = None
  observed_at: UTCInstant

  @field_validator("favored_at", "observed_at", mode="before")
  @classmethod
  def utc_string(cls, value: object) -> object:
    if not isinstance(value, str) or not value.endswith("Z"):
      raise ValueError("来源时间必须是 UTC ISO 字符串")
    return value


BilibiliRecord = User | Folder | Video | Membership


def record_title(value: BilibiliRecord) -> str:
  if isinstance(value, User):
    return value.name or f"UID {value.mid}"
  if isinstance(value, Folder):
    return value.title or f"收藏夹 {value.id}"
  if isinstance(value, Video):
    return value.title or value.bvid or f"av{value.aid}"
  return "已失效的收藏条目" if value.availability == "unavailable" else "收藏条目"


__all__ = [
  "FOLDER_RESOLVER",
  "MEMBERSHIP_RESOLVER",
  "USER_RESOLVER",
  "VIDEO_RESOLVER",
  "BilibiliRecord",
  "Folder",
  "Membership",
  "User",
  "Video",
  "record_title",
]
