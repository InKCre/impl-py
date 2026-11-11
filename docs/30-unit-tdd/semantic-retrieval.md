# Semantic Retrieval

Shared capability names, external behavior, Peer interoperability, and wire contracts are owned by
`../_shared/20-product-tdd/`. This document owns only core-py's internal implementation and derived
projection mechanics; if the two disagree, fix the shared owner first and then this local projection.

## Purpose

This unit lets a caller rank existing info-base entities by semantic similarity. It returns real `BlockModel` and
`RelationModel` rows plus scores; it does not create transient chunks, synthesize an answer, or repair the info-base while
reading.

## Durable Facts And Runtime Owners

```text
AIProvider -> AIModel -> EmbeddingProfile
                              |
Block/Resolver projection ----+----> BlockEmbedding
RelationService projection ---+----> RelationEmbedding
                              |
                              +----> SemanticRetrievalManager
```

- `AIProvider` stores one deployment-scoped dialect configuration. `AIModel` names one provider-native model and its typed
  effective capabilities. `EmbeddingProfile` owns one mutable vector-space contract: model plus required dimensions.
- Provider credentials remain database-owned configuration inside the current deployment trust boundary. Dialect runtime
  models type secret fields as secrets，and adapters unwrap a value only when constructing the provider client。This keeps
  ordinary model representation on the secret-aware path；a repository-wide observability redaction boundary is separate
  infrastructure and is not claimed by this unit。
- Block/Relation rows remain information authority. Embedding records are profile-scoped derived support and can always be
  rebuilt.
- `AIManager` is graph-blind. It routes typed `embed` or `chat` requests through the selected model/provider and exact
  dialect adapter; it does not choose Blocks, assemble relation meaning, or own retrieval policy.
- Deployment config selects an optional default profile. Callers may select a profile explicitly. A missing or dangling
  selection fails at use time rather than making config own the profile lifecycle.

## Semantic Projection

- A Block candidate is available only when its exact Resolver can provide `get_text()`. Retrieval never falls back to
  direct `Block.content`, even when content happens to be inline text.
- A Relation candidate is projected as directed semantics: from-Block label, exact relation content, and to-Block label.
  Resolver `get_label()` is Block-local and bounded; it cannot traverse relations or materialize graph state.
- Resolver IDs select compatibility contracts but do not appear in semantic text. Labels use the Resolver-owned friendly
  kind, such as `feed item <title>` or `github user <username>`.
- Unsupported projection is an ordinary unavailable candidate, not a reason to stop a maintenance scan.

## Record Maintenance And Freshness

`SemanticRetrievalManager.maintain()` scans deterministic ID pages for missing or stale Block and Relation records. It
projects and embeds outside a database transaction, then upserts only a complete valid provider batch in a short
transaction. A provider failure writes no partial batch.

`rebuild()` uses its invocation timestamp as a cutoff and replaces records older than that snapshot. The methods do not
create a job, dirty table, retry loop, lease, or hidden collection hook. Exact typed maintain/rebuild Job handlers call the
same methods and persist bounded reports in Job state；Cron may materialize those Jobs from ordinary templates。

A Block record is usable only when its timestamp is at least the Profile and Block timestamps and its vector has the exact
Profile dimension. A Relation record must additionally be at least both endpoint Block timestamps. This is best-effort
database-row freshness: externally mutable storage bytes do not update a Block and therefore cannot be inferred from these
timestamps.

## Retrieval

`SemanticRetrievalManager.retrieve()` is the domain facade. Local execution embeds one text query, compares exact cosine
distance against fresh compatible records, merges Block and Relation candidates into one stable global order, applies the
caller limit/threshold/type controls, and returns no more than the requested bound. Retrieval never calls `maintain()`.

`VectorRetrievalOptions.filters` accepts the shared exact-AND `BlockQueryFilters` (`resolver.eq`, strict signed-INTEGER
`storage.eq`). Block predicates enter candidate SQL before cosine ranking/top-K. Any actual Block predicate suppresses the
Relation candidate query entirely; an empty filter has no effect. SDK/facade/inbound/delegation preserve the same typed
options, including omitted predicates. No post-top-K filtering or oversampling is used. Pure-condition MCP queries bypass
semantic retrieval and embeddings altogether.

When a caller explicitly targets another Peer, the same facade delegates exact capability
`core.semantic_retrieval.v1`. The provider inbound invokes `retrieve_local()` and cannot recursively delegate. Generic
Peer routing remains payload-opaque and only fails over after proven non-execution; an uncertain post-dispatch outcome
stops.

## Acceptance

The checked-in acceptance corpus is owned by
`tests/semantic_retrieval/acceptance/corpus/manifest.json`. It uses the real Memos API, RSS/Atom source jobs, HTTP full-text
and enclosure paths, PostgreSQL binary storage, and a pinned public-domain SQLite Architecture HTML snapshot. Readable
aliases exist only in the manifest/harness and are resolved to actual database IDs after producer execution.

The deterministic vertical proves producer graph creation, rumination Tool/GraphForm control flow, global ranking gates
and complete cleanup. It is not semantic-quality authority. Real quality requires an explicit credentialed run:

```bash
INKCRE_TEST_DATABASE_URL=... \
INKCRE_ACCEPTANCE_AI_API_KEY=... \
INKCRE_ACCEPTANCE_AI_BASE_URL=... \
INKCRE_ACCEPTANCE_EMBEDDING_MODEL=... \
INKCRE_ACCEPTANCE_CHAT_MODEL=... \
pdm run test:acceptance
```

Each judged query requires a primary Block in the global top three and above every explicit distractor. The rumination
query additionally requires the specific derived Pager Block to outrank its coarse source document. Provider responses,
vectors, and generated graph rows are never committed as authority.

## Explicit Non-Goals

- ANN/HNSW indexes, pagination, cross-profile score fusion, hybrid retrieval, and graph-navigation retrieval
- Chat/RAG answer generation or a generic AI proxy
- transient chunk/segment persistence
- generic capability invocation endpoints or delegation jobs
- persistent Agent Thread/checkpoint infrastructure

数据库查询与 embedding upsert 由 `inkcre_core/persistence/semantic_retrieval/` 执行。每个 provider batch
先完成向量数量与维度校验，再在同一短事务中分别批量写入 Block 和 Relation embeddings；
任一种写入失败都会回滚整个 batch。profile 与检索读取同样原生 await，不使用同步 session。
