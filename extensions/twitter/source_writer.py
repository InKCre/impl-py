"""Positive Bookmark observations, separate from the shared native archive."""

from app.business.source import SourceManager
from app.persistence.source.uow import SourceUnitOfWork
from app.schemas.info_base.relation import RelationCreateForm
from app.schemas.source import SourceModel

from .model import TwitterBatch
from .reconcile import KINDS, persist_twitter


async def persist_bookmarks(
  batch: TwitterBatch,
  primary_post_ids: tuple[str, ...],
  source: SourceModel,
  uow: SourceUnitOfWork,
) -> None:
  """Add Source-scoped evidence in the caller's locked Source transaction.

  Only admitted records were collected; identity-only or archive-retained roots
  in the reconciliation result are not observations by this Source. Bookmark
  evidence is positive history, not a complete snapshot of remote membership.
  """
  if not set(primary_post_ids) <= {post.id for post in batch.posts}:
    raise ValueError("Bookmark primary IDs must have captured Post records")
  anchor = await SourceManager.ensure_block_async(source, uow)
  if anchor.id is None:
    raise RuntimeError("Source anchor has no persisted identity")
  result = await persist_twitter(batch, uow.graph, text_policy="supplement")
  desired = {
    (result.block_ids[kind][record.id], "collects")
    for kind in KINDS
    for record in getattr(batch, kind)
  }
  desired.update(
    (result.block_ids["posts"][native_id], "bookmarks") for native_id in primary_post_ids
  )
  existing = {
    (relation.to_, relation.content)
    for relation in await uow.graph.relations.get_outgoing_many(
      (anchor.id,), to_ids={target for target, _ in desired}
    )
  }
  await uow.graph.relations.create_many(
    RelationCreateForm(from_=anchor.id, to_=target, content=content)
    for target, content in sorted(desired - existing)
  )
