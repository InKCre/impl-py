## Semantic retrieval domain

This use-domain module owns graph-to-text projection coordination, EmbeddingProfile selection, profile-scoped derived
records, maintenance/rebuild, exact cosine ranking, and the public Block/Relation result contract.

- Graph rows remain authority. Embedding records are successful derived support only; never persist unavailable/failed as
  entity state and never repair records implicitly inside `retrieve()`.
- Block input comes only from its Resolver `get_text()`. Relation input comes only from `RelationService.get_text()` and
  preserves directed subject/property/value semantics through Block-local labels.
- Freshness is timestamp- and dimension-derived. Relation records depend on the Relation plus both endpoint Blocks.
- Projection/storage/provider work occurs outside database transactions. Persist one completely validated provider batch
  through one short atomic upsert transaction.
- `maintain` scans past unavailable entities. `rebuild` uses its start timestamp as the cutoff. Neither creates a job,
  lease, dirty flag, retry, rollback, or compensation lifecycle.
- `retrieve_local` is the non-delegating provider path. `retrieve` is the domain facade that later Peer routing may wrap;
  fixed HTTP inbound code must call the local path to avoid delegation loops.
