# Lexical Retrieval

## Purpose

Lexical retrieval recalls existing Blocks from exact textual clues: identifiers, names,
filenames, contiguous Chinese fragments, authored text, transcripts, subtitles, OCR and
other Resolver-projected features. It returns real `BlockModel` rows with bounded evidence;
it does not synthesize an answer or create transient search entities.

Graph navigation remains the owner of facts that Organization has already expressed as
Blocks and Relations. Lexical retrieval can still find the same fact while it exists only
inside one Block-local projection. Hybrid retrieval is a later composition of atomic recall
capabilities, not a mode hidden inside this manager.

## Authority And Projection

```text
Block + exact Resolver -- get_label/get_text(context="lexical") --> block_lexical_records
                                                               |
query ---------------------------------------------------------+--> existing Blocks
```

- `BlockModel` plus graph relations remains information authority.
- `BlockLexicalRecordModel` is a rebuildable, one-row-per-Block projection owned only by
  `LexicalRetrievalManager`. Its Block foreign key is both record identity and cascade-delete
  boundary.
- `label` is the highest-weight, concise Block identity supplied by the Resolver. `text` is
  optional Block-local lexical content. `search_vector` is derived from both and is not a
  second information authority.
- `context="lexical"` is non-recursive. A parent Block does not copy complete child text
  merely because a relation makes that text reachable. Each content child owns its own
  record, avoiding mechanical parent/child duplicates while preserving graph navigation.
- Retrieval and maintenance never read `Block.content` directly. Storage hydration, solved
  content and any permitted faithful materialization stay behind the exact Resolver.

## Materialization And Organization

Maintenance calls `get_text(context="lexical", materialize_missing=True)`. A Resolver may
therefore lazily add a faithful semantic-content child such as `text`, `transcript` or
`subtitle` before the new child is indexed by a later scan. Lexical retrieval does not own
OCR/ASR/provider selection, the graph write or a media-specific fallback ladder.

Media description and summarization are interpretation rather than faithful
materialization. Exact Job `core.organization.media_interpretation.v1` scans media Blocks
without an `interpretation` result, selects an independently configured image/audio/video
Agent, sends canonical multimodal message parts and allows only the Agent's existing graph
Tool to add an interpretation graph. Lexical maintenance subsequently indexes those real
text Blocks. The Organization path never writes lexical records directly.

Canonical AI messages own `TextContentPart`, `ImageContentPart`, `AudioContentPart` and
`VideoContentPart`. Storage may provide an optional transfer URL as a transport hint; bytes
remain the fallback authority. Dialect adapters alone translate those parts to provider wire
formats. `core.alibaba-model-studio.v1` is the current exact multimodal adapter; it does not
broaden `core.openai-compatible.v1`.

## Record Maintenance

`maintain()` scans deterministic Block-ID pages for absent or timestamp-stale records.
`rebuild()` additionally selects records older than its invocation cutoff. Projection work
happens before a short upsert transaction; unsupported/unavailable Blocks are skipped and
bounded diagnostics distinguish them from failures.

The exact typed Jobs are:

- `core.feature_retrieval.lexical.maintain.v1`
- `core.feature_retrieval.lexical.rebuild.v1`

Cron may create either Job through the same generic Job template. The manager methods do not
create Jobs themselves, and Block writes do not implicitly maintain the projection. This
keeps active organization/materialization separate from ordinary retrieval reads while
allowing scheduled, explicit or delegated maintenance callers to share one implementation.

## Ranking And Capability

Local retrieval combines four explainable evidence classes in this order:

1. case-insensitive exact label;
2. label substring;
3. text substring;
4. PostgreSQL `simple` text-search term match.

`pg_trgm` GIN indexes support substring recall, including contiguous Chinese fragments
without promising Chinese tokenization. The weighted `tsvector` supports primitive term
recall. Results are stable by evidence class, text-search rank and Block ID, capped at 20,
and expose `label`, bounded plain-text `excerpt`, `evidence` and numeric `rank`.

Optional `BlockQueryFilters` (`resolver.eq`, strict signed-INTEGER `storage.eq`) are exact AND predicates over Block rows,
applied inside candidate SQL before evidence ordering/top-K. Omitted predicates stay omitted through request serialization;
unknown fields/operators and null equalities are rejected. These controls do not imply Resolver availability. The MCP
`query` condition-only branch belongs to info-base and does not read lexical records.

`LexicalRetrievalManager.retrieve()` is the uniform local/delegating facade. Exact capability
`core.feature_retrieval.lexical.v1` uses the generic Peer protocol; the provider inbound calls
`retrieve_local()` and cannot delegate recursively. Retrieval never invokes maintenance.

## Acceptance

The primary acceptance journeys use ordinary producer, Resolver, Storage, Organization and
Job paths. They cover exact identifiers, unsegmented Chinese fragments, unmaterialized mail
attachment metadata, update/delete freshness, PDF body recall, real image/audio/video
textualization, Agent-authored media interpretation and Peer delegation.

The multimodal authority is NASA asset
`GSFC_20140121_GPM_m11457_Dave_McComas`. The harness pins origin URLs, ETags and digests,
derives bounded local media assets, and keeps those outputs ignored. Production code never
contains corpus aliases, expected phrases or test-shaped IDs.

## Explicit Non-Goals

- semantic or graph-navigation ranking inside lexical retrieval
- hybrid ranking or answer generation
- Relation lexical records
- a Chinese tokenizer promise
- recursive graph indexing
- implicit maintenance during a retrieval request

数据库查询与 projection upsert 由 `inkcre_core/persistence/lexical_retrieval/` 执行。业务入口原生 await，
每个维护 batch 在短事务中一次 upsert 全部有效投影；Resolver 投影不占用该写事务。
