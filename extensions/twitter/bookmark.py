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


class BackfillConfig(pydantic.BaseModel):
  """Bound one explicit traversal of the current bookmark ordering."""

  model_config = pydantic.ConfigDict(extra="forbid")

  skip: int = pydantic.Field(default=0, ge=0, le=10000)
  limit: int = pydantic.Field(default=200, ge=1, le=1000)
  page_size: int = pydantic.Field(default=50, ge=1, le=100)


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
  backfill_config_cls=BackfillConfig,
):
  """Twitter Bookmark as Source"""

  API_BASE_URL = "https://api.x.com/2"

  async def collect(self, job: JobModel, config: pydantic.BaseModel) -> None:
    """Collect all new bookmarks and its notes.

    What is new bookmarks?
    The tweets before the last collected tweet. The last collected tweet
    is the latest created_at tweet block.
    (Potential issue if bookmarks order changes)

    What is bookmark note?
    User can add a note to a bookmark by replying the bookmark tweet.

    Docs https://docs.x.com/x-api/bookmarks/get-bookmarks
    """
    collect_config = typing.cast(CollectConfig, config)
    full = collect_config.full
    result_limit = collect_config.result_limit

    page = job.state.get("page") if job.state else None

    api_client = await TwitterAPI.new()
    bookmarks_res = await api_client.get_bookmarks(page=page, max_results=result_limit)

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

    # Update job state for next page if full and has next_page
    if full and bookmarks_res.next_page and bookmarks_res.next_page != page:
      job.state = job.state or {}
      job.state["page"] = bookmarks_res.next_page

  async def backfill(self, job: JobModel, config: pydantic.BaseModel) -> None:
    """Fetch a bounded range without advancing the ordinary collection marker."""
    backfill = typing.cast(BackfillConfig, config)
    api_client = await TwitterAPI.new()
    page = None
    scanned = saved = pages = 0
    end = backfill.skip + backfill.limit
    cursors: set[str] = set()
    seen: set[str] = set()
    job.state = {"scanned": 0, "saved": 0, "pages": 0, "exhausted": False}

    while scanned < end:
      response = await api_client.get_bookmarks(
        page=page, max_results=min(backfill.page_size, end - scanned)
      )
      ids = response.primary_post_ids
      if seen.intersection(ids) or len(set(ids)) != len(ids):
        raise ValueError("Bookmark ordering changed during backfill; retry explicitly")
      if response.next_page and response.next_page in cursors:
        raise ValueError("X returned a repeated bookmark pagination token")
      if not ids and response.next_page:
        raise ValueError("X returned an empty bookmark page with a continuation token")
      selected = ids[max(0, backfill.skip - scanned) : end - scanned]
      if selected:
        async with source_uow() as uow:
          source = await uow.sources.get(self._id, lock=True)
          if source is None:
            raise ValueError("Twitter Source no longer exists")
          await persist_twitter(
            _selected_batch(response.batch, selected),
            uow.graph,
            text_policy="supplement",
          )
      seen.update(ids)
      scanned += len(ids)
      saved += len(selected)
      pages += 1
      page = response.next_page or None
      job.state = {
        "scanned": scanned,
        "saved": saved,
        "pages": pages,
        "exhausted": page is None,
        "next_page": page,
      }
      if page is None:
        break
      cursors.add(page)
