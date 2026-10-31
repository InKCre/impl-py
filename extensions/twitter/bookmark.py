"""Twitter Bookmark Source"""

import pydantic
import sqlmodel
import typing
from app.business.source import SourceBase
from app.persistence.source.uow import source_uow
from app.schemas.job import JobModel
from .api import TwitterAPI
from .model import TwitterBatch
from .reconcile import persist_twitter


class SourceConfig(sqlmodel.SQLModel):
  """Configuration for Twitter Bookmark Source."""

  ...


class CollectConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  full: bool = False
  result_limit: int = pydantic.Field(default=40, ge=5, le=100)


def _selected_batch(batch: TwitterBatch, primary_ids: tuple[str, ...]) -> TwitterBatch:
  posts = {post.id: post for post in batch.posts}
  selected = set(primary_ids)
  pending = list(primary_ids)
  while pending:
    post = posts[pending.pop()]
    references = [post.repostOf]
    if post.quote is not None:
      references.append(post.quote.postId)
    if post.replyTo is not None:
      references.append(post.replyTo.postId)
    for native_id in references:
      if native_id is not None and native_id in posts and native_id not in selected:
        selected.add(native_id)
        pending.append(native_id)
  chosen = [post for post in batch.posts if post.id in selected]
  authors = {post.authorId for post in chosen}
  media_ids = {native_id for post in chosen for native_id in post.mediaIds or ()}
  for post in chosen:
    if post.article is not None:
      media_ids.update(post.article.mediaIds or ())
      if post.article.coverMediaId is not None:
        media_ids.add(post.article.coverMediaId)
  return TwitterBatch(
    posts=chosen,
    users=[user for user in batch.users if user.id in authors],
    media=[media for media in batch.media if media.id in media_ids],
  )


class Source(
  SourceBase[SourceConfig],
  config_cls=SourceConfig,
  collect_config_cls=CollectConfig,
):
  """Twitter Bookmark as Source"""

  API_BASE_URL = "https://api.x.com/2"

  async def collect(self, job: JobModel, config: pydantic.BaseModel) -> None:
    """Collect one recent page or traverse all pages for an explicit full Job."""
    collect_config = typing.cast(CollectConfig, config)
    full = collect_config.full
    result_limit = collect_config.result_limit

    page = job.state.get("page") if job.state else None
    seen_pages = {page} if page else set()
    api_client = await TwitterAPI.new()
    while True:
      bookmarks_res = await api_client.get_bookmarks(page=page, max_results=result_limit)
      next_page = bookmarks_res.next_page
      if full and next_page and next_page in seen_pages:
        raise ValueError("Twitter bookmark pagination repeated a cursor")

      async with source_uow() as uow:
        source = await uow.sources.get(self._id, lock=True)
        if source is None:
          raise ValueError("Twitter Source no longer exists")
        primary_ids = bookmarks_res.primary_post_ids
        selected = primary_ids
        if not full:
          latest_id = str((source.state or {}).get("latest_tweet_id", ""))
          if latest_id in primary_ids:
            selected = primary_ids[: primary_ids.index(latest_id)]
        if selected:
          await persist_twitter(
            _selected_batch(bookmarks_res.batch, selected),
            uow.graph,
            text_policy="supplement",
          )
        if not full and primary_ids:
          source.state = {
            **(source.state or {}),
            "latest_tweet_id": primary_ids[0],
          }
          await uow.sources.save(source)

      if not full:
        return
      # Jobs execute once. Persist each page before advancing diagnostics, and
      # finish traversal here rather than leaving continuation to a future Job.
      job.state = {**(job.state or {}), "page": next_page}
      if not next_page:
        return
      seen_pages.add(next_page)
      page = next_page
