"""Graph application operations shared by transports and composing use cases."""

from inkcre_core.schemas.info_base.main import (
  GraphBlockIDMapping,
  GraphForm,
  SubmitGraphResult,
  StarsGraphForm,
)
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.info_base.relation import RelationCreateForm

from inkcre_core.persistence.info_base.uow import GraphUnitOfWork, graph_uow


async def persist_graph(graph: GraphForm, uow: GraphUnitOfWork) -> SubmitGraphResult:
  """Add a flat graph to the supplied unit of work without ending its transaction."""
  blocks = await uow.blocks.create_many(graph.blocks)
  mappings: list[GraphBlockIDMapping] = []
  for form, block in zip(graph.blocks, blocks, strict=True):
    if block.id is None:
      raise RuntimeError("Inserted Block is missing its database ID")
    mappings.append(GraphBlockIDMapping(local_id=form.id, id=block.id))
  persisted_ids = {mapping.local_id: mapping.id for mapping in mappings}
  await uow.relations.create_many(
    RelationCreateForm(
      from_=persisted_ids.get(form.from_, form.from_),
      to_=persisted_ids.get(form.to_, form.to_),
      content=form.content,
    )
    for form in graph.relations
  )
  return SubmitGraphResult(blocks=tuple(mappings))


async def submit_graph(graph: GraphForm) -> SubmitGraphResult:
  """Commit one complete flat graph before returning its Block identity mapping."""
  async with graph_uow() as uow:
    result = await persist_graph(graph, uow)
  return result


async def get_related_block(
  block_id: int, *, content: str, outgoing: bool = True
) -> BlockModel | None:
  async with graph_uow() as uow:
    return await uow.blocks.get_related(block_id, content=content, outgoing=outgoing)


async def persist_stars(graph: StarsGraphForm, uow: GraphUnitOfWork) -> BlockModel:
  """Reconcile exact resolver identities and relations within one transaction."""
  from .resolver import ResolverManager

  candidate = BlockModel.model_validate(graph.block)
  existing = await ResolverManager.get(candidate).get_existing_async(uow.blocks)
  block = existing if existing is not None else await uow.blocks.create(graph.block)
  if block.id is None:
    raise RuntimeError("Persisted star Block is missing its database ID")
  for arc in graph.out_arcs:
    target = await persist_stars(arc.to_graph, uow)
    if target.id is None:
      raise RuntimeError("Persisted star Block is missing its database ID")
    await uow.relations.fetchsert(
      RelationModel(from_=block.id, to_=target.id, content=arc.relation.content)
    )
  for arc in graph.in_arcs:
    source = await persist_stars(arc.from_graph, uow)
    if source.id is None:
      raise RuntimeError("Persisted star Block is missing its database ID")
    await uow.relations.fetchsert(
      RelationModel(from_=source.id, to_=block.id, content=arc.relation.content)
    )
  return block


async def submit_stars(graph: StarsGraphForm) -> BlockModel:
  async with graph_uow() as uow:
    result = await persist_stars(graph, uow)
  return result
