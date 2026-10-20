"""Twitter-owned persisted facts, independent of capture-provider wire contracts."""

from __future__ import annotations

import datetime
import re
from typing import Annotated, Literal

import pydantic


TwitterID = Annotated[str, pydantic.Field(strict=True, pattern=r"^[1-9][0-9]*$")]
Count = Annotated[int, pydantic.Field(strict=True, ge=0)]
Number = Annotated[int | float, pydantic.Field(allow_inf_nan=False)]


def _validate_instant(value: str) -> str:
  if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
    raise ValueError("must be a UTC timestamp with second precision")
  datetime.datetime.fromisoformat(value)
  return value


def _validate_observed_at(value: str) -> str:
  if not re.fullmatch(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z", value
  ):
    raise ValueError("must be a UTC ISO-8601 timestamp including seconds")
  datetime.datetime.fromisoformat(value)
  return value


Instant = Annotated[str, pydantic.AfterValidator(_validate_instant)]
ObservedAt = Annotated[str, pydantic.AfterValidator(_validate_observed_at)]


def _validate_range(value: tuple[int, int]) -> tuple[int, int]:
  if value[1] < value[0]:
    raise ValueError("range end before start")
  return value


Range = Annotated[
  tuple[Count, Count],
  pydantic.Field(strict=False),
  pydantic.AfterValidator(_validate_range),
]
Rect = Annotated[tuple[Count, Count, Count, Count], pydantic.Field(strict=False)]


class _TwitterModel(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

  @pydantic.field_validator("*")
  @classmethod
  def reject_explicit_null(cls, value: object) -> object:
    if value is None:
      raise ValueError("omit unknown fields instead of sending null")
    return value


class UrlEntity(_TwitterModel):
  type: Literal["url"]
  range: Range
  expanded: str


class MentionEntity(_TwitterModel):
  type: Literal["mention"]
  range: Range
  userId: TwitterID


class HashtagEntity(_TwitterModel):
  type: Literal["hashtag"]
  range: Range


class CashtagEntity(_TwitterModel):
  type: Literal["cashtag"]
  range: Range
  name: str | None = None
  ticker: str | None = None


class TimestampEntity(_TwitterModel):
  type: Literal["timestamp"]
  range: Range


Entity = Annotated[
  UrlEntity | MentionEntity | HashtagEntity | CashtagEntity | TimestampEntity,
  pydantic.Field(discriminator="type"),
]


class TextStyle(_TwitterModel):
  range: Range
  styles: list[str] = pydantic.Field(min_length=1)


class RichText(_TwitterModel):
  text: str
  visible: Range | None = None
  entities: list[Entity] | None = pydantic.Field(default=None, min_length=1)
  styles: list[TextStyle] | None = pydantic.Field(default=None, min_length=1)


class UserCounts(_TwitterModel):
  followers: Count | None = None
  following: Count | None = None
  posts: Count | None = None
  mediaPosts: Count | None = None
  likes: Count | None = None


class UserCountsObservedAt(_TwitterModel):
  followers: ObservedAt | None = None
  following: ObservedAt | None = None
  posts: ObservedAt | None = None
  mediaPosts: ObservedAt | None = None
  likes: ObservedAt | None = None


class Affiliation(_TwitterModel):
  name: str
  url: str | None = None
  badgeUrl: str | None = None
  kind: str | None = None
  ownerId: TwitterID | None = None


class Verification(_TwitterModel):
  blue: bool | None = None
  type: str | None = None


class Professional(_TwitterModel):
  type: str | None = None
  categories: list[str] | None = pydantic.Field(default=None, min_length=1)


class TwitterUser(_TwitterModel):
  id: TwitterID
  handle: str
  name: str
  createdAt: Instant | None = None
  avatarUrl: str | None = None
  avatarShape: str | None = None
  bannerUrl: str | None = None
  bio: RichText | None = None
  location: str | None = None
  website: str | None = None
  pinnedPostIds: list[TwitterID] | None = pydantic.Field(default=None, min_length=1)
  protected: bool | None = None
  sensitive: bool | None = None
  interstitial: str | None = None
  withheldIn: list[str] | None = pydantic.Field(default=None, min_length=1)
  verified: Verification | None = None
  affiliation: Affiliation | None = None
  parody: str | None = None
  professional: Professional | None = None
  counts: UserCounts | None = None
  countsObservedAt: UserCountsObservedAt | None = None


class VideoVariant(_TwitterModel):
  url: str
  type: str
  bitrate: Count | None = None


class MediaTag(_TwitterModel):
  userId: TwitterID
  handle: str | None = None


class Video(_TwitterModel):
  durationMs: Count | None = None
  variants: list[VideoVariant] = pydantic.Field(min_length=1)


class TwitterMedia(_TwitterModel):
  id: TwitterID
  type: Literal["photo", "video", "gif"]
  url: str
  width: Count | None = None
  height: Count | None = None
  alt: str | None = None
  crops: list[Rect] | None = pydantic.Field(default=None, min_length=1)
  faces: list[Rect] | None = pydantic.Field(default=None, min_length=1)
  tags: list[MediaTag] | None = pydantic.Field(default=None, min_length=1)
  video: Video | None = None
  sourcePostId: TwitterID | None = None
  sourceUserId: TwitterID | None = None
  sensitive: list[str] | None = pydantic.Field(default=None, min_length=1)
  unavailable: str | None = None
  title: str | None = None
  description: str | None = None
  link: str | None = None


class PollChoice(_TwitterModel):
  label: str
  count: Count | None = None


class Poll(_TwitterModel):
  choices: list[PollChoice] = pydantic.Field(min_length=2)
  endsAt: Instant | None = None
  final: bool | None = None
  updatedAt: Instant | None = None


class CardImage(_TwitterModel):
  url: str
  width: Count | None = None
  height: Count | None = None
  alt: str | None = None


class CardPlayer(_TwitterModel):
  url: str
  width: Count | None = None
  height: Count | None = None


class Card(_TwitterModel):
  type: str
  url: str
  title: str | None = None
  description: str | None = None
  domain: str | None = None
  image: CardImage | None = None
  siteUserId: TwitterID | None = None
  creatorUserId: TwitterID | None = None
  player: CardPlayer | None = None
  poll: Poll | None = None
  extra: dict[str, str | bool] | None = None


class ArticleBlock(_TwitterModel):
  type: str
  text: str
  styles: list[Annotated[tuple[Count, Count, str], pydantic.Field(strict=False)]] | None = (
    pydantic.Field(default=None, min_length=1)
  )
  entities: (
    list[Annotated[tuple[Count, Count, Count], pydantic.Field(strict=False)]] | None
  ) = pydantic.Field(default=None, min_length=1)
  data: dict[str, pydantic.JsonValue] | None = None


class ArticleEntity(_TwitterModel):
  type: str
  data: dict[str, pydantic.JsonValue]


class Article(_TwitterModel):
  id: TwitterID
  title: str
  preview: str | None = None
  coverMediaId: TwitterID | None = None
  publishedAt: Instant | None = None
  modifiedAt: Instant | None = None
  blocks: list[ArticleBlock] | None = pydantic.Field(default=None, min_length=1)
  entities: list[ArticleEntity] | None = pydantic.Field(default=None, min_length=1)
  mediaIds: list[TwitterID] | None = pydantic.Field(default=None, min_length=1)


class Place(_TwitterModel):
  id: str
  name: str | None = None
  fullName: str | None = None
  country: str | None = None
  countryCode: str | None = None
  type: str | None = None
  bbox: (
    Annotated[tuple[Number, Number, Number, Number], pydantic.Field(strict=False)] | None
  ) = None


class PostCounts(_TwitterModel):
  replies: Count | None = None
  reposts: Count | None = None
  quotes: Count | None = None
  likes: Count | None = None
  bookmarks: Count | None = None
  views: Count | None = None


class PostCountsObservedAt(_TwitterModel):
  replies: ObservedAt | None = None
  reposts: ObservedAt | None = None
  quotes: ObservedAt | None = None
  likes: ObservedAt | None = None
  bookmarks: ObservedAt | None = None
  views: ObservedAt | None = None


class ReplyTo(_TwitterModel):
  postId: TwitterID
  userId: TwitterID | None = None
  handle: str | None = None
  tombstone: str | None = pydantic.Field(default=None, min_length=1)


class Quote(_TwitterModel):
  postId: TwitterID
  url: str | None = None
  tombstone: str | None = pydantic.Field(default=None, min_length=1)


class CommunityNote(_TwitterModel):
  id: TwitterID | None = None
  title: str | None = None
  text: RichText
  lang: str | None = None
  url: str | None = None


class ContentWarning(_TwitterModel):
  title: str
  text: str | None = None


class Community(_TwitterModel):
  id: TwitterID
  name: str | None = None


class AIGenerated(_TwitterModel):
  media: bool
  source: str


class TwitterPost(_TwitterModel):
  id: TwitterID
  authorId: TwitterID
  createdAt: Instant
  repostOf: TwitterID | None = None
  conversationId: TwitterID | None = None
  replyTo: ReplyTo | None = None
  quote: Quote | None = None
  text: RichText | None = None
  lang: str | None = None
  source: str | None = None
  mediaIds: list[TwitterID] | None = pydantic.Field(default=None, min_length=1)
  card: Card | None = None
  article: Article | None = None
  place: Place | None = None
  counts: PostCounts | None = None
  countsObservedAt: PostCountsObservedAt | None = None
  editIds: list[TwitterID] | None = pydantic.Field(default=None, min_length=2)
  communityNote: CommunityNote | None = None
  sensitive: bool | None = None
  contentWarning: ContentWarning | None = None
  community: Community | None = None
  replyPolicy: str | None = None
  paidPromotion: bool | None = None
  aiGenerated: AIGenerated | None = None


class TwitterIdentity(_TwitterModel):
  """A known native identity without captured entity content."""

  id: TwitterID


class TwitterBatch(_TwitterModel):
  """Facts captured for one common reconcile operation; transport limits stay outside."""

  users: list[TwitterUser] = pydantic.Field(default_factory=list)
  posts: list[TwitterPost] = pydantic.Field(default_factory=list)
  media: list[TwitterMedia] = pydantic.Field(default_factory=list)


TwitterRecord = TwitterUser | TwitterPost | TwitterMedia
TextPolicy = Literal["replace", "supplement"]


__all__ = [
  "TextPolicy",
  "TwitterBatch",
  "TwitterID",
  "TwitterIdentity",
  "TwitterMedia",
  "TwitterPost",
  "TwitterRecord",
  "TwitterUser",
]
