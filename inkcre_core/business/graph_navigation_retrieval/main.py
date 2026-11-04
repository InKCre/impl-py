"""Bounded, presentation-neutral navigation over persisted graph authority."""

from collections import deque
import typing


from inkcre_core.persistence.info_base.uow import GraphUnitOfWork, graph_uow
from inkcre_core.schemas.graph_navigation_retrieval import (
  DEFAULT_NEIGHBORHOOD_LIMIT,
  MAX_NEIGHBORHOOD_LIMIT,
  DEFAULT_MAX_HOPS,
  MAX_MAX_HOPS,
  DEFAULT_MAX_EXPLORED_BLOCKS,
  MAX_MAX_EXPLORED_BLOCKS,
  DEFAULT_MAX_EXPLORED_RELATIONS,
  BlockNeighborhood,
  ConnectedComponentsResult,
  ConnectedSeedComponent,
  GraphDirection,
  GraphModel,
  PathFound,
  PathLimitReached,
  PathNotFound,
  PathResult,
  RelationNeighborhood,
)
from inkcre_core.schemas.info_base.block import BlockID, BlockModel
from inkcre_core.schemas.info_base.relation import RelationID, RelationModel


FRONTIER_QUERY_SIZE = 200


class GraphNavigationRetrievalManager:
  """Own bounded graph-navigation queries over persisted entities."""

  @classmethod
  async def get_random_block(
    cls,
  ) -> BlockModel | None:
    async with graph_uow() as uow:
      return await uow.blocks.get_random()

  @classmethod
  async def get_block_neighborhood(
    cls,
    focal_block: BlockID,
    *,
    direction: GraphDirection = "both",
    contents: typing.Collection[str] = (),
    limit: int = DEFAULT_NEIGHBORHOOD_LIMIT,
    cursor: RelationID | None = None,
  ) -> BlockNeighborhood | None:
    if not 1 <= limit <= MAX_NEIGHBORHOOD_LIMIT:
      raise ValueError(f"limit must be between 1 and {MAX_NEIGHBORHOOD_LIMIT}")
    async with graph_uow() as uow:
      focal = await uow.blocks.get(focal_block)
      if focal is None:
        return None

      requested = limit + 1
      branches: list[tuple[RelationModel, ...]] = []
      if direction in ("out", "both"):
        branches.append(
          await uow.relations.get_endpoint_page(
            (focal_block,),
            endpoint="from",
            contents=contents,
            cursor=cursor,
            limit=requested,
          )
        )
      if direction in ("in", "both"):
        branches.append(
          await uow.relations.get_endpoint_page(
            (focal_block,), endpoint="to", contents=contents, cursor=cursor, limit=requested
          )
        )
      by_id = {
        relation.id: relation
        for branch in branches
        for relation in branch
        if relation.id is not None
      }
      ordered = sorted(
        by_id.values(),
        key=lambda relation: typing.cast(int, relation.id),
        reverse=True,
      )
      page = ordered[:requested]
      has_more = len(page) > limit
      page = page[:limit]
      endpoint_ids = {focal_block}
      for relation in page:
        endpoint_ids.update((relation.from_, relation.to_))
      blocks = await uow.blocks.get_many(endpoint_ids)
      existing_ids = {block.id for block in blocks}
      closed_relations = tuple(
        relation
        for relation in page
        if relation.from_ in existing_ids and relation.to_ in existing_ids
      )
      next_cursor = typing.cast(RelationID, page[-1].id) if has_more and page else None
      return BlockNeighborhood(
        focal_block=focal_block,
        graph=GraphModel(blocks=blocks, relations=closed_relations),
        next_cursor=next_cursor,
      )

  @classmethod
  async def get_relation_neighborhood(
    cls,
    focal_relation: RelationID,
  ) -> RelationNeighborhood | None:
    async with graph_uow() as uow:
      relation = await uow.relations.get_by_id(focal_relation)
      if relation is None:
        return None
      blocks = await uow.blocks.get_many((relation.from_, relation.to_))
      if {block.id for block in blocks} != {relation.from_, relation.to_}:
        return None
      return RelationNeighborhood(
        focal_relation=focal_relation,
        graph=GraphModel(blocks=blocks, relations=(relation,)),
      )

  @classmethod
  async def get_connected_components(
    cls,
    seed_block_ids: typing.Collection[BlockID],
    *,
    contents: typing.Collection[str],
    max_explored_blocks: int = DEFAULT_MAX_EXPLORED_BLOCKS,
    max_explored_relations: int = DEFAULT_MAX_EXPLORED_RELATIONS,
  ) -> ConnectedComponentsResult:
    """Partition existing seeds by bounded undirected exact-content reachability."""
    seeds = tuple(dict.fromkeys(seed_block_ids))
    relation_contents = tuple(dict.fromkeys(contents))
    if not relation_contents:
      raise ValueError("contents must not be empty")
    if max_explored_blocks < 1 or max_explored_relations < 1:
      raise ValueError("exploration bounds must be positive")
    if len(seeds) > max_explored_blocks:
      raise ValueError("seed blocks exceed max_explored_blocks")
    async with graph_uow() as uow:
      existing_blocks = await uow.blocks.get_many(seeds)
      existing_seed_ids = {block.id for block in existing_blocks if block.id is not None}
      missing = tuple(seed for seed in seeds if seed not in existing_seed_ids)
      assigned_seeds: set[BlockID] = set()
      explored_blocks = set(existing_seed_ids)
      seen_relations: set[RelationID] = set()
      explored_relation_count = 0
      proof_relations: dict[RelationID, RelationModel] = {}
      components: list[ConnectedSeedComponent] = []
      truncated = False

      for seed in seeds:
        if seed not in existing_seed_ids or seed in assigned_seeds:
          continue
        if truncated:
          components.append(
            ConnectedSeedComponent(seed_block_ids=(seed,), member_block_ids=(seed,))
          )
          assigned_seeds.add(seed)
          continue

        members = {seed}
        frontier = deque((seed,))
        while frontier and not truncated:
          current = frontier.popleft()
          for endpoint in ("from", "to"):
            cursor: RelationID | None = None
            while not truncated:
              remaining = max_explored_relations - explored_relation_count
              if remaining == 0:
                truncated = True
                break
              requested = min(FRONTIER_QUERY_SIZE, remaining + 1)
              page = await uow.relations.get_endpoint_page(
                (current,),
                endpoint=typing.cast(typing.Literal["from", "to"], endpoint),
                contents=relation_contents,
                cursor=cursor,
                limit=requested,
              )
              if len(page) > remaining:
                page = page[:remaining]
                truncated = True
              explored_relation_count += len(page)
              for relation in page:
                if relation.id is None or relation.id in seen_relations:
                  continue
                relation_id = relation.id
                seen_relations.add(relation_id)
                neighbor = relation.to_ if relation.from_ == current else relation.from_
                if neighbor in members:
                  continue
                if (
                  neighbor not in explored_blocks
                  and len(explored_blocks) >= max_explored_blocks
                ):
                  truncated = True
                  break
                members.add(neighbor)
                explored_blocks.add(neighbor)
                frontier.append(neighbor)
                proof_relations[relation_id] = relation
              if truncated or len(page) < requested:
                break
              cursor = typing.cast(RelationID, page[-1].id)

        component_seeds = tuple(seed_id for seed_id in seeds if seed_id in members)
        assigned_seeds.update(component_seeds)
        components.append(
          ConnectedSeedComponent(
            seed_block_ids=component_seeds,
            member_block_ids=tuple(sorted(members)),
          )
        )

      proof_blocks = await uow.blocks.get_many(explored_blocks)
      return ConnectedComponentsResult(
        components=tuple(components),
        proof_graph=GraphModel(
          blocks=proof_blocks,
          relations=tuple(proof_relations.values()),
        ),
        missing_seed_block_ids=missing,
        truncated=truncated,
      )

  @classmethod
  async def find_path(  # noqa: PLR0913
    cls,
    from_block: BlockID,
    to_block: BlockID,
    *,
    direction: GraphDirection = "both",
    contents: typing.Collection[str] = (),
    max_hops: int = DEFAULT_MAX_HOPS,
    max_explored_blocks: int = DEFAULT_MAX_EXPLORED_BLOCKS,
  ) -> PathResult:
    if not 0 <= max_hops <= MAX_MAX_HOPS:
      raise ValueError(f"max_hops must be between 0 and {MAX_MAX_HOPS}")
    if not 1 <= max_explored_blocks <= MAX_MAX_EXPLORED_BLOCKS:
      raise ValueError(
        f"max_explored_blocks must be between 1 and {MAX_MAX_EXPLORED_BLOCKS}"
      )
    async with graph_uow() as uow:
      endpoints = await uow.blocks.get_many((from_block, to_block))
      if {block.id for block in endpoints} != {from_block, to_block}:
        return PathNotFound()
      if from_block == to_block:
        return PathFound(
          graph=GraphModel(blocks=endpoints, relations=()),
          block_path=(from_block,),
          relation_path=(),
        )

      forward_parents: dict[BlockID, tuple[BlockID, RelationID] | None] = {from_block: None}
      backward_next: dict[BlockID, tuple[BlockID, RelationID] | None] = {to_block: None}
      forward_depths = {from_block: 0}
      backward_depths = {to_block: 0}
      forward_frontier = {from_block}
      backward_frontier = {to_block}
      traversed_relations: dict[RelationID, RelationModel] = {}

      while forward_frontier and backward_frontier:
        forward_level = forward_depths[next(iter(forward_frontier))]
        backward_level = backward_depths[next(iter(backward_frontier))]
        if forward_level + backward_level >= max_hops:
          return PathLimitReached()

        expand_forward = len(forward_frontier) <= len(backward_frontier)
        if expand_forward:
          next_frontier, meeting = await cls._expand_path_frontier(
            forward_frontier,
            direction=direction,
            contents=contents,
            reverse=False,
            own_steps=forward_parents,
            own_depths=forward_depths,
            other_depths=backward_depths,
            max_hops=max_hops,
            traversed_relations=traversed_relations,
            uow=uow,
          )
          forward_frontier = next_frontier
        else:
          next_frontier, meeting = await cls._expand_path_frontier(
            backward_frontier,
            direction=direction,
            contents=contents,
            reverse=True,
            own_steps=backward_next,
            own_depths=backward_depths,
            other_depths=forward_depths,
            max_hops=max_hops,
            traversed_relations=traversed_relations,
            uow=uow,
          )
          backward_frontier = next_frontier
        explored = set(forward_parents) | set(backward_next)
        if len(explored) > max_explored_blocks:
          return PathLimitReached()
        if meeting is not None:
          return await cls._assemble_bidirectional_path(
            from_block,
            to_block,
            meeting=meeting,
            forward_parents=forward_parents,
            backward_next=backward_next,
            traversed_relations=traversed_relations,
            uow=uow,
          )
      return PathNotFound()

  @classmethod
  async def _expand_path_frontier(  # noqa: PLR0913
    cls,
    frontier: set[BlockID],
    *,
    direction: GraphDirection,
    contents: typing.Collection[str],
    reverse: bool,
    own_steps: dict[BlockID, tuple[BlockID, RelationID] | None],
    own_depths: dict[BlockID, int],
    other_depths: dict[BlockID, int],
    max_hops: int,
    traversed_relations: dict[RelationID, RelationModel],
    uow: GraphUnitOfWork,
  ) -> tuple[set[BlockID], BlockID | None]:
    next_frontier: set[BlockID] = set()
    meeting: BlockID | None = None
    best_hops: int | None = None
    frontier_items = tuple(frontier)
    for chunk_start in range(0, len(frontier), FRONTIER_QUERY_SIZE):
      chunk = frontier_items[chunk_start : chunk_start + FRONTIER_QUERY_SIZE]
      chunk_members = set(chunk)
      relations = await cls._frontier_relations(
        chunk,
        direction=direction,
        contents=contents,
        reverse=reverse,
        uow=uow,
      )
      for relation in relations:
        relation_id = typing.cast(RelationID, relation.id)
        traversed_relations[relation_id] = relation
        for current, neighbor in cls._relation_steps(
          relation, frontier=chunk_members, direction=direction, reverse=reverse
        ):
          if neighbor in own_steps:
            continue
          own_steps[neighbor] = (current, relation_id)
          own_depths[neighbor] = own_depths[current] + 1
          next_frontier.add(neighbor)
          if neighbor not in other_depths:
            continue
          hops = own_depths[neighbor] + other_depths[neighbor]
          if hops <= max_hops and (best_hops is None or hops < best_hops):
            best_hops = hops
            meeting = neighbor
    return next_frontier, meeting

  @staticmethod
  def _relation_steps(
    relation: RelationModel,
    *,
    frontier: set[BlockID],
    direction: GraphDirection,
    reverse: bool,
  ) -> tuple[tuple[BlockID, BlockID], ...]:
    steps: list[tuple[BlockID, BlockID]] = []
    if not reverse:
      if direction in ("out", "both") and relation.from_ in frontier:
        steps.append((relation.from_, relation.to_))
      if direction in ("in", "both") and relation.to_ in frontier:
        steps.append((relation.to_, relation.from_))
    else:
      if direction in ("out", "both") and relation.to_ in frontier:
        steps.append((relation.to_, relation.from_))
      if direction in ("in", "both") and relation.from_ in frontier:
        steps.append((relation.from_, relation.to_))
    return tuple(steps)

  @classmethod
  async def _frontier_relations(
    cls,
    frontier: typing.Collection[BlockID],
    *,
    direction: GraphDirection,
    contents: typing.Collection[str],
    reverse: bool = False,
    uow: GraphUnitOfWork,
  ) -> tuple[RelationModel, ...]:
    relations: dict[RelationID, RelationModel] = {}
    needs_from = (
      direction in ("out", "both")
      if not reverse
      else direction
      in (
        "in",
        "both",
      )
    )
    needs_to = (
      direction in ("in", "both")
      if not reverse
      else direction
      in (
        "out",
        "both",
      )
    )
    if needs_from:
      for relation in await uow.relations.get_endpoint_page(
        frontier, endpoint="from", contents=contents, limit=MAX_MAX_EXPLORED_BLOCKS
      ):
        if relation.id is not None:
          relations[relation.id] = relation
    if needs_to:
      for relation in await uow.relations.get_endpoint_page(
        frontier, endpoint="to", contents=contents, limit=MAX_MAX_EXPLORED_BLOCKS
      ):
        if relation.id is not None:
          relations[relation.id] = relation
    return tuple(relations.values())

  @classmethod
  async def _assemble_bidirectional_path(  # noqa: PLR0913
    cls,
    from_block: BlockID,
    to_block: BlockID,
    *,
    meeting: BlockID,
    forward_parents: dict[BlockID, tuple[BlockID, RelationID] | None],
    backward_next: dict[BlockID, tuple[BlockID, RelationID] | None],
    traversed_relations: dict[RelationID, RelationModel],
    uow: GraphUnitOfWork,
  ) -> PathResult:
    block_path: list[BlockID] = [meeting]
    relation_path: list[RelationID] = []
    current = meeting
    while current != from_block:
      parent = forward_parents[current]
      if parent is None:  # pragma: no cover - reconstruction invariant
        raise RuntimeError("Path reconstruction lost its parent")
      current, relation_id = parent
      block_path.append(current)
      relation_path.append(relation_id)
    block_path.reverse()
    relation_path.reverse()
    current = meeting
    while current != to_block:
      next_step = backward_next[current]
      if next_step is None:  # pragma: no cover - reconstruction invariant
        raise RuntimeError("Path reconstruction lost its next step")
      current, relation_id = next_step
      block_path.append(current)
      relation_path.append(relation_id)
    blocks = await uow.blocks.get_many(block_path)
    relations = tuple(
      relation
      for relation_id in relation_path
      if (relation := traversed_relations.get(relation_id)) is not None
    )
    if {block.id for block in blocks} != set(block_path) or len(relations) != len(
      relation_path
    ):
      return PathNotFound()
    return PathFound(
      graph=GraphModel(blocks=blocks, relations=relations),
      block_path=tuple(block_path),
      relation_path=tuple(relation_path),
    )
