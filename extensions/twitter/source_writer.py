"""Account-centered Bookmark evidence, separate from the shared native archive."""

from inkcre_core.business.source import SourceManager
from inkcre_core.persistence.source.uow import SourceUnitOfWork
from inkcre_core.schemas.info_base.relation import RelationCreateForm
from inkcre_core.schemas.source import SourceModel

from .model import TwitterBatch, TwitterIdentity
from .reconcile import persist_twitter


async def persist_bookmarks(
  batch: TwitterBatch,
  primary_post_ids: tuple[str, ...],
  bookmark_user_id: str,
  source: SourceModel,
  uow: SourceUnitOfWork,
) -> None:
  """Add positive history in the caller's locked Source transaction.

  The actor belongs to this fetch, not the Source's current authorization or a
  captured Post's author. Only selected, captured primary Posts are Bookmarks;
  reconciliation identities and changed records cannot establish membership.
  """
  actor = TwitterIdentity(id=bookmark_user_id)
  if not set(primary_post_ids) <= {post.id for post in batch.posts}:
    raise ValueError("Bookmark primary IDs must have captured Post records")
  if not primary_post_ids:
    return
  anchor = await SourceManager.ensure_block_async(source, uow)
  if anchor.id is None:
    raise RuntimeError("Source anchor has no persisted identity")
  result = await persist_twitter(
    batch, uow.graph, text_policy="supplement", user_identities=(actor,)
  )
  actor_id = result.block_ids["users"][actor.id]
  desired = {(anchor.id, actor_id, "collects")}
  desired.update(
    (actor_id, result.block_ids["posts"][native_id], "bookmarks")
    for native_id in primary_post_ids
  )
  existing = {
    (relation.from_, relation.to_, relation.content)
    for relation in await uow.graph.relations.get_outgoing_many(
      (anchor.id, actor_id), to_ids={target for _, target, _ in desired}
    )
  }
  await uow.graph.relations.create_many(
    RelationCreateForm(from_=origin, to_=target, content=content)
    for origin, target, content in sorted(desired - existing)
  )
