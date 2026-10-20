import json
import typing
from typing import Optional as Opt

import pydantic

from app.business.info_base.resolver import Resolver, ResolverManager, TextProjectionContext
from app.business.info_base.resolver.label import format_label
from app.business.info_base.services import BlockService
from app.schemas.info_base.block import BlockForm
from app.schemas.info_base.main import StarsGraphForm
from .schema import Tweet
from .model import TwitterIdentity, TwitterMedia, TwitterPost, TwitterUser
from .reconcile import MEDIA_RESOLVER, POST_RESOLVER, REFERENCE_SCHEMA, USER_RESOLVER


class TweetResolver(Resolver[Tweet, str], rso_type="extensions.twitter.tweet.v1"):
  """Tweet Resolver."""

  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> Tweet:
    """Get the solved content (non-cache).

    - attachments will be resolved from `attachment:` out relations
    """
    # Get the base tweet without attachments
    tweet_dict = json.loads(await self.get_raw_content(refresh=refresh))
    tweet = Tweet(**tweet_dict)

    # Fetch attachments
    relations = await self.get_relations(include_in=False, refresh=refresh)
    tweet.attachments = []
    tweet.links = []
    attachments = {
      block.id: block
      for block in await BlockService.get_many(
        tuple(
          relation.to_
          for relation in relations
          if relation.content.startswith("attachment:")
          or relation.content == "entities:url"
        )
      )
    }
    for relation in relations:
      if relation.content.startswith("attachment:") or relation.content == "entities:url":
        block = attachments.get(relation.to_)
        if block is not None:
          attachment_resolver = ResolverManager.get(block)
          solved = await attachment_resolver.get_solved_content(
            refresh=refresh,
            materialize_missing=materialize_missing,
          )
          if relation.content.startswith("attachment:"):
            tweet.attachments.append(solved)
          elif isinstance(solved, str):
            tweet.links.append(solved)

    return tweet

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    """Return the text of the tweet."""
    del context
    solved = await self.get_solved_content(
      refresh=refresh,
      materialize_missing=materialize_missing,
    )
    return solved.text

  async def get_label(self, *, refresh: bool = False) -> str:
    root = Tweet.model_validate_json(await self.get_raw_content(refresh=refresh))
    return format_label(
      "tweet",
      root.text or str(root.id),
      first_line=True,
    )

  @classmethod
  def create_block(cls, content: Tweet, storage: Opt[int] = None) -> BlockForm:
    """Create a BlockForm from Tweet."""
    # Remove attachments from the dict for storage
    tweet_dict = content.model_dump()
    tweet_dict.pop("attachments", None)
    tweet_dict.pop("links", None)
    return BlockForm(
      resolver=cls.__rsotype__,
      content=json.dumps(tweet_dict),
      storage=storage,
    )

  @classmethod
  def create_graph(cls, content: Tweet) -> StarsGraphForm:
    """Create a StarsGraphForm from Tweet."""
    block = cls.create_block(content)
    return StarsGraphForm(
      block=block,
      out_arcs=(),
    )


Native = typing.TypeVar("Native", bound=pydantic.BaseModel)


async def _native_content(
  resolver: Resolver, model: type[Native], *, refresh: bool
) -> Native | TwitterIdentity:
  raw = await resolver.get_raw_content(refresh=refresh)
  value = json.loads(raw)
  if isinstance(value, dict) and set(value) == {"id"}:
    return TwitterIdentity.model_validate(value)
  return model.model_validate(value)


class ArchivePostResolver(
  Resolver[TwitterPost | TwitterIdentity, str], rso_type=POST_RESOLVER
):
  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> TwitterPost | TwitterIdentity:
    del materialize_missing
    return await _native_content(self, TwitterPost, refresh=refresh)

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del context, materialize_missing
    post = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    parts = [f"https://x.com/i/status/{post.id}", post.id]
    if isinstance(post, TwitterIdentity):
      return "\n".join(parts)
    parts.append(f"author: {post.authorId}")
    for relation in await self.get_relations(include_in=False, refresh=refresh):
      try:
        reference = json.loads(relation.content)
      except ValueError:
        continue
      if reference != {"schema": REFERENCE_SCHEMA, "type": "author"}:
        continue
      author = await BlockService.get(relation.to_)
      if author is not None and author.resolver == USER_RESOLVER:
        value = json.loads(author.content)
        if set(value) != {"id"}:
          user = TwitterUser.model_validate(value)
          parts.append(f"{user.name} @{user.handle}")
      break
    if post.text is not None:
      parts.append(post.text.text)
    if post.article is not None:
      parts.append(post.article.title)
      if post.article.blocks is not None:
        parts.extend(block.text for block in post.article.blocks)
      elif post.article.preview is not None:
        parts.append(post.article.preview)
    return "\n".join(parts)

  async def get_label(self, *, refresh: bool = False) -> str:
    post = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    text = (
      post.text.text if isinstance(post, TwitterPost) and post.text is not None else post.id
    )
    return format_label("tweet", text or post.id, first_line=True)


class ArchiveUserResolver(
  Resolver[TwitterUser | TwitterIdentity, str], rso_type=USER_RESOLVER
):
  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> TwitterUser | TwitterIdentity:
    del materialize_missing
    return await _native_content(self, TwitterUser, refresh=refresh)

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del context, materialize_missing
    user = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    if isinstance(user, TwitterIdentity):
      return user.id
    return "\n".join(
      value
      for value in (
        user.id,
        f"{user.name} @{user.handle}",
        f"https://x.com/{user.handle}",
        user.bio.text if user.bio is not None else None,
        user.location,
        user.website,
      )
      if value is not None
    )

  async def get_label(self, *, refresh: bool = False) -> str:
    user = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    return format_label(
      "twitter user", f"@{user.handle}" if isinstance(user, TwitterUser) else user.id
    )


class ArchiveMediaResolver(
  Resolver[TwitterMedia | TwitterIdentity, str], rso_type=MEDIA_RESOLVER
):
  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> TwitterMedia | TwitterIdentity:
    del materialize_missing
    return await _native_content(self, TwitterMedia, refresh=refresh)

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    del context, materialize_missing
    media = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    if isinstance(media, TwitterIdentity):
      return media.id
    return "\n".join(
      value
      for value in (media.id, media.alt, media.title, media.description)
      if value is not None
    )

  async def get_label(self, *, refresh: bool = False) -> str:
    media = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    label = (
      media.title or media.alt or media.id if isinstance(media, TwitterMedia) else media.id
    )
    return format_label("twitter media", label, first_line=True)
