"""Fixed x-monkey 0.2.0 DTOs; these do not define Twitter persisted semantics."""

from __future__ import annotations

import datetime
import re
from typing import Annotated, Generic, Literal, TypeVar

import pydantic


ArchiveID = Annotated[str, pydantic.Field(strict=True, pattern=r"^[1-9][0-9]*$")]
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


class _WireModel(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

  @pydantic.field_validator("*")
  @classmethod
  def reject_explicit_null(cls, value: object) -> object:
    if value is None:
      raise ValueError("omit unknown fields instead of sending null")
    return value


class UrlEntity(_WireModel):
  type: Literal["url"]
  range: Range
  expanded: str


class MentionEntity(_WireModel):
  type: Literal["mention"]
  range: Range
  userId: ArchiveID


class HashtagEntity(_WireModel):
  type: Literal["hashtag"]
  range: Range


class CashtagEntity(_WireModel):
  type: Literal["cashtag"]
  range: Range
  name: str | None = None
  ticker: str | None = None


class TimestampEntity(_WireModel):
  type: Literal["timestamp"]
  range: Range


Entity = Annotated[
  UrlEntity | MentionEntity | HashtagEntity | CashtagEntity | TimestampEntity,
  pydantic.Field(discriminator="type"),
]


class TextStyle(_WireModel):
  range: Range
  styles: list[str] = pydantic.Field(min_length=1)


class RichText(_WireModel):
  text: str
  visible: Range | None = None
  entities: list[Entity] | None = pydantic.Field(default=None, min_length=1)
  styles: list[TextStyle] | None = pydantic.Field(default=None, min_length=1)


class UserCounts(_WireModel):
  followers: Count | None = None
  following: Count | None = None
  posts: Count | None = None
  mediaPosts: Count | None = None
  likes: Count | None = None


class UserCountsObservedAt(_WireModel):
  followers: ObservedAt | None = None
  following: ObservedAt | None = None
  posts: ObservedAt | None = None
  mediaPosts: ObservedAt | None = None
  likes: ObservedAt | None = None


class Affiliation(_WireModel):
  name: str
  url: str | None = None
  badgeUrl: str | None = None
  kind: str | None = None
  ownerId: ArchiveID | None = None


class Verification(_WireModel):
  blue: bool | None = None
  type: str | None = None


class Professional(_WireModel):
  type: str | None = None
  categories: list[str] | None = pydantic.Field(default=None, min_length=1)


class ArchiveUser(_WireModel):
  id: ArchiveID
  handle: str
  name: str
  createdAt: Instant | None = None
  avatarUrl: str | None = None
  avatarShape: str | None = None
  bannerUrl: str | None = None
  bio: RichText | None = None
  location: str | None = None
  website: str | None = None
  pinnedPostIds: list[ArchiveID] | None = pydantic.Field(default=None, min_length=1)
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


class VideoVariant(_WireModel):
  url: str
  type: str
  bitrate: Count | None = None


class MediaTag(_WireModel):
  userId: ArchiveID
  handle: str | None = None


class Video(_WireModel):
  durationMs: Count | None = None
  variants: list[VideoVariant] = pydantic.Field(min_length=1)


class ArchiveMedia(_WireModel):
  id: ArchiveID
  type: Literal["photo", "video", "gif"]
  url: str
  width: Count | None = None
  height: Count | None = None
  alt: str | None = None
  crops: list[Rect] | None = pydantic.Field(default=None, min_length=1)
  faces: list[Rect] | None = pydantic.Field(default=None, min_length=1)
  tags: list[MediaTag] | None = pydantic.Field(default=None, min_length=1)
  video: Video | None = None
  sourcePostId: ArchiveID | None = None
  sourceUserId: ArchiveID | None = None
  sensitive: list[str] | None = pydantic.Field(default=None, min_length=1)
  unavailable: str | None = None
  title: str | None = None
  description: str | None = None
  link: str | None = None


class PollChoice(_WireModel):
  label: str
  count: Count | None = None


class Poll(_WireModel):
  choices: list[PollChoice] = pydantic.Field(min_length=2)
  endsAt: Instant | None = None
  final: bool | None = None
  updatedAt: Instant | None = None


class CardImage(_WireModel):
  url: str
  width: Count | None = None
  height: Count | None = None
  alt: str | None = None


class CardPlayer(_WireModel):
  url: str
  width: Count | None = None
  height: Count | None = None


class Card(_WireModel):
  type: str
  url: str
  title: str | None = None
  description: str | None = None
  domain: str | None = None
  image: CardImage | None = None
  siteUserId: ArchiveID | None = None
  creatorUserId: ArchiveID | None = None
  player: CardPlayer | None = None
  poll: Poll | None = None
  extra: dict[str, str | bool] | None = None


class ArticleBlock(_WireModel):
  type: str
  text: str
  styles: list[Annotated[tuple[Count, Count, str], pydantic.Field(strict=False)]] | None = (
    pydantic.Field(default=None, min_length=1)
  )
  entities: (
    list[Annotated[tuple[Count, Count, Count], pydantic.Field(strict=False)]] | None
  ) = pydantic.Field(default=None, min_length=1)
  data: dict[str, pydantic.JsonValue] | None = None


class ArticleEntity(_WireModel):
  type: str
  data: dict[str, pydantic.JsonValue]


class Article(_WireModel):
  id: ArchiveID
  title: str
  preview: str | None = None
  coverMediaId: ArchiveID | None = None
  publishedAt: Instant | None = None
  modifiedAt: Instant | None = None
  blocks: list[ArticleBlock] | None = pydantic.Field(default=None, min_length=1)
  entities: list[ArticleEntity] | None = pydantic.Field(default=None, min_length=1)
  mediaIds: list[ArchiveID] | None = pydantic.Field(default=None, min_length=1)


class Place(_WireModel):
  id: str
  name: str | None = None
  fullName: str | None = None
  country: str | None = None
  countryCode: str | None = None
  type: str | None = None
  bbox: (
    Annotated[tuple[Number, Number, Number, Number], pydantic.Field(strict=False)] | None
  ) = None


class PostCounts(_WireModel):
  replies: Count | None = None
  reposts: Count | None = None
  quotes: Count | None = None
  likes: Count | None = None
  bookmarks: Count | None = None
  views: Count | None = None


class PostCountsObservedAt(_WireModel):
  replies: ObservedAt | None = None
  reposts: ObservedAt | None = None
  quotes: ObservedAt | None = None
  likes: ObservedAt | None = None
  bookmarks: ObservedAt | None = None
  views: ObservedAt | None = None


class ReplyTo(_WireModel):
  postId: ArchiveID
  userId: ArchiveID | None = None
  handle: str | None = None
  tombstone: str | None = pydantic.Field(default=None, min_length=1)


class Quote(_WireModel):
  postId: ArchiveID
  url: str | None = None
  tombstone: str | None = pydantic.Field(default=None, min_length=1)


class CommunityNote(_WireModel):
  id: ArchiveID | None = None
  title: str | None = None
  text: RichText
  lang: str | None = None
  url: str | None = None


class ContentWarning(_WireModel):
  title: str
  text: str | None = None


class Community(_WireModel):
  id: ArchiveID
  name: str | None = None


class AIGenerated(_WireModel):
  media: bool
  source: str


class ArchivePost(_WireModel):
  id: ArchiveID
  authorId: ArchiveID
  createdAt: Instant
  repostOf: ArchiveID | None = None
  conversationId: ArchiveID | None = None
  replyTo: ReplyTo | None = None
  quote: Quote | None = None
  text: RichText | None = None
  lang: str | None = None
  source: str | None = None
  mediaIds: list[ArchiveID] | None = pydantic.Field(default=None, min_length=1)
  card: Card | None = None
  article: Article | None = None
  place: Place | None = None
  counts: PostCounts | None = None
  countsObservedAt: PostCountsObservedAt | None = None
  editIds: list[ArchiveID] | None = pydantic.Field(default=None, min_length=2)
  communityNote: CommunityNote | None = None
  sensitive: bool | None = None
  contentWarning: ContentWarning | None = None
  community: Community | None = None
  replyPolicy: str | None = None
  paidPromotion: bool | None = None
  aiGenerated: AIGenerated | None = None


class ArchiveBatch(_WireModel):
  users: list[ArchiveUser] = pydantic.Field(default_factory=list, max_length=1000)
  posts: list[ArchivePost] = pydantic.Field(default_factory=list, max_length=1000)
  media: list[ArchiveMedia] = pydantic.Field(default_factory=list, max_length=1000)


class ArchiveQuery(_WireModel):
  users: list[ArchiveID] = pydantic.Field(default_factory=list, max_length=1000)
  posts: list[ArchiveID] = pydantic.Field(default_factory=list, max_length=1000)
  media: list[ArchiveID] = pydantic.Field(default_factory=list, max_length=1000)


T = TypeVar("T")


class QueryResult(_WireModel, Generic[T]):
  id: ArchiveID
  status: Literal["found", "missing", "failed"]
  data: T | None = None
  error: str | None = None


class QueryResponse(_WireModel):
  users: list[QueryResult[ArchiveUser]]
  posts: list[QueryResult[ArchivePost]]
  media: list[QueryResult[ArchiveMedia]]


class SubmitResult(_WireModel):
  id: ArchiveID
  status: Literal["accepted", "unchanged", "partial", "failed"]
  reason: str | None = None


class SubmitSummary(_WireModel):
  accepted: Count
  unchanged: Count
  partial: Count
  failed: Count
  total: Count


class SubmitResponse(_WireModel):
  summary: SubmitSummary
  users: list[SubmitResult]
  posts: list[SubmitResult]
  media: list[SubmitResult]


class SessionResponse(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", strict=True)

  authenticated: bool
  registered: bool
  username: str | None
  expires_at: str | None = None
  account_url: str | None = None


__all__ = [
  "ArchiveBatch",
  "ArchiveID",
  "ArchiveMedia",
  "ArchivePost",
  "ArchiveQuery",
  "ArchiveUser",
  "QueryResponse",
  "QueryResult",
  "SessionResponse",
  "SubmitResponse",
  "SubmitResult",
  "SubmitSummary",
]
