# Graph Navigation Retrieval

## Purpose

`GraphNavigationRetrievalManager` is the core-py realization of bounded navigation over persisted
Blocks and Relations. It is a use-domain module: it reads existing graph authority and returns
presentation-neutral structure. It does not call Resolvers, materialize graph changes, rank semantic
similarity, own route state, or delegate to another Peer.

## Read Contract

`GraphModel` is endpoint-closed: every returned Relation has both endpoint Blocks in the same model.
It is distinct from producer `GraphForm`; a read never carries placeholders or proposes writes.

- A Block neighborhood reads separately bounded incoming and outgoing Relation pages, merges them by
  descending Relation ID, then obtains endpoints in one Block query. Its cursor belongs to the ordered
  Relation page even if a concurrently removed endpoint causes one Relation to be omitted.
- A Relation neighborhood returns the exact Relation and both endpoints, or no result.
- Path retrieval returns `found`, `not_found`, or `limit_reached`. A found result additionally carries
  ordered Block and Relation paths and an endpoint-closed `GraphModel`.

Direction and Relation-content constraints prune traversal. They never rewrite persisted direction or
become presentation hints.

查询入口均为异步业务方法，每次调用拥有独立的 GraphUnitOfWork。HTTP、MCP 与 Agent Tool
等待业务方法，不创建 session；内部遍历复用该次操作的 repositories，不接受可选 raw session。

Agent-facing controllers 与输入模型由本领域拥有，并调用相同 Manager methods：
`get_entity_neighborhood`、`find_path`、`get_connected_components`。它们只负责 Agent 参数接合与 JSON
projection，不复制遍历逻辑，也不让 Manager 依赖 Agent runtime。Organization、MCP 和 Agent Query 只是这些
能力的消费者。

## Query Mechanics

Relation pages use `(from_, id DESC)` and `(to_, id DESC)` indexes. A `both` neighborhood intentionally
uses two endpoint queries; one broad `OR + ORDER BY id` query can select the primary-key index and
discard most rows on sparse endpoints.

Shortest-by-hop search is bounded bidirectional BFS. Defaults are four hops and 1,000 explored Blocks;
hard bounds are eight hops and 10,000 Blocks. Frontier batching is private transport/query mechanics.
Endpoint Relation pages continue until exhausted: many parallel Relations can refer to very few
Blocks, so a full Relation page cannot establish disconnection or replace the explored-Block budget.
Equal-shortest results have no stable tie-break contract. Traversal retains the Relation rows it reads
and assembles a candidate from that operation-local snapshot instead of rereading and rejecting ordinary
concurrent changes. If candidate endpoint Blocks no longer exist when closure is assembled, the public
result is `not_found`.

Random focal selection uses count plus a stable-ID offset query. It does not transfer all Block IDs and
does not promise statistical quality from a small authority.

## Acceptance

Public-manager integration runs against migrated PostgreSQL and asserts topology properties rather than
private SQL or incidental equal-path choice. Sparse transaction-local load probes establish endpoint
index use. The shared Hub corpus owns aliases and graph shape; production models and methods do not.
