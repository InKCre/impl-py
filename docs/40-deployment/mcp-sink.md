# MCP Sink Operations

## Configure And Run

1. Through the Peer-authenticated Core management API, create `core.mcp.v1` with config `{ "pat": "..." }`.
2. Enable that Sink for the current Peer. Its MCP URL is the Peer's public Core base URL plus `/sinks/{sink_id}/mcp`.
3. Configure the MCP Host or operator tunnel to send `Authorization: Bearer <pat>`.
4. Disable the Sink before deleting it. Disable removes the endpoint immediately from this process; durable intent for other
   Peers is unchanged.

The PAT is deployment-scoped and intentionally simple for the single-owner MVP. It is not a Peer JWT and is not copied into
Peer advertisement. Rotating config updates the running instance without changing its endpoint.

## ChatGPT Developer Mode

Use a Secure MCP Tunnel when the Core endpoint should not be exposed directly. The tunnel owns public reachability and PAT
injection; core-py does not contain tunnel-specific branches. Connect the tunnel URL in ChatGPT Developer Mode, then use
**Scan Tools** to import the server's `use-inkcre` Skill snapshot.

With the current OpenAI `tunnel-client`, the minimal HTTP target is:

```bash
export INKCRE_MCP_PAT='<sink PAT>'
export CONTROL_PLANE_API_KEY='<OpenAI tunnel API key>'

tunnel-client run \
  --control-plane.tunnel-id='<tunnel id>' \
  --control-plane.api-key='env:CONTROL_PLANE_API_KEY' \
  --mcp.server-url='https://<core-origin>/sinks/<sink-id>/mcp' \
  --mcp.extra-headers='Authorization: env:INKCRE_MCP_PAT' \
  --mcp.discovery-extra-headers='Authorization: env:INKCRE_MCP_PAT'
```

Runtime and discovery headers are both configured because the startup initialize probe also reaches the PAT-protected MCP
endpoint. Header values use the tool's `env:` reference rather than literal argv values. See the upstream
[tunnel-client configuration](https://github.com/openai/tunnel-client/blob/master/docs/configuration.md) for installation、
profiles and current control-plane credentials. Acceptance exercises the real tunnel/ChatGPT path manually rather than
freezing a model Tool sequence.

## Start Without IDs

The evidence tools are `meta_discover`, `query`, `open_entities`, `read_blocks`, `expand_entities`, `find_path`,
`resolver_methods`, `invoke_resolver_methods`. There are no old-name aliases: refresh client tool snapshots after upgrading.
Sink type, endpoint and Resource URIs are unchanged.

Call `meta_discover` with `collection="resolvers"`, `"source_types"`, `"storage_types"`, or `"storages"` (no Source-instance
or Block collection). Resolver identities come solely from this Peer's registry, without querying Blocks, and grant no
behavior capability. Type `registered_locally` flags mean code presence, not health/execution guarantees. Storage rows
contain only `id`, `type` and nullable, nonunique bounded `nickname`; `id` is the key for subsequent Block queries.

For example, after discovering Storage ID -2:

```json
{"filters":{"storage":{"eq":-2}},"limit":20}
```

Call `query` with this input, then pass its `next_cursor` with the same filters. Add `resolver.eq` for exact AND selection;
known historical Resolver IDs are queryable even if absent from the catalog. Without text, queries require at least one
filter and reject retrieval modes; they directly read Block metadata, independent of retrieval records, without relevance
scores. Storage IDs must be signed INTEGERs, not strings/bools/null. No other operators are supported.
For text retrieval, supply nonblank `query` and explicit nonempty `modes` (`lexical`/`semantic`), optional filters, limit
`1..20`, and no cursor. Each owner applies Block filters before ranking/top-K, excluding Relations when filtered.

Both metadata and condition-only pages use `limit=1..100` (default 20). Cursors cannot be reused across tools/conditions.
Pagination observes live data, not a snapshot. Pages exclude config/state/schema/URL/content/pointers and never construct
handlers or fetch external resources. Use returned `block:<id>` refs with `open_entities`, then IDs with `read_blocks`;
content/method reads are separate and may materialize or fetch bytes under their existing contracts.

Deploy migrations `0ab4b22c6d3b` and `bb5dcb562587` before use at scale. They create `blocks(resolver,id)` and
`blocks(storage,id)` using transactional index builds; plan a maintenance window because Block writes may wait.
AND queries can use one index and check the other predicate; costs still depend on selectivity. Reverting the app restores
its previous tool surface; additive indexes may remain or be downgraded independently without deleting rows.

Automated disposable-database acceptance is part of `pdm run test:database`: mounted TCP HTTP/PAT with normal
Core/SinkManager lifecycle, real graph producers and retrieval maintenance, deterministic embedding dialect, metadata →
query → open → read, adversarial pre-top-K filters, PAT rejection and shutdown cleanup. A skipped E2E is not acceptance;
ChatGPT/tunnel remains separate manual verification.

## System resources and keyword index maintenance

Pass `collection="sources"` to `system_resources` to find a Source ID. For a Telegram Source, read its
bound user, attachment setting, pinned bot ID and last update ID where present. To inspect the latest collection
Jobs for Source 17, use:

```json
{"collection":"jobs","job_filters":{"source":17,"type":"core.source.collect.v1"},"limit":10}
```

Use `{"collection":"jobs","id":"19"}` for an exact Job. For lists, pass `next_cursor` unchanged with the same
collection and filters. Pages contain at most 100 rows. The other collections are `crons`, `peers`,
`extensions`, `sinks`, `agents`, `ai_models` and `embedding_profiles`. Registered Source code, an active Peer
lease, an enabled Sink or Extension selection, and a running instance are different facts.

Resource reads work with the existing PAT. They expose allowlisted operational fields. Raw config, generic Job
payload/state, free-text provider errors, and credentials are omitted. For Telegram, Job counts and diagnostic scopes
are available. Use the deployment's administrative diagnostics for full logs.

Call `lexical_index_status` to see whether any Block has a missing or outdated projection and to read recent,
pending and running jobs. Some Resolver types cannot produce a projection. Each Job's counters describe that batch.

To let a trusted MCP client submit work, add `"allow_lexical_maintenance": true` beside its Sink's `pat`. It is
false by default. `submit_lexical_maintenance(operation="maintain")` queues a bounded batch for missing or stale
records. `operation="rebuild"` also refreshes existing records and may fetch external content. The tool returns a
Job ID. A capable Core worker must claim it. Poll `system_resources` to read the true lifecycle and report. If
`next_after_block` is present, submit the same operation with `continue_from_job` set to the completed Job's ID.
Upgrade every Core worker that executes lexical Jobs before submitting continuation batches.
Starting a fresh maintenance pass gives previously unavailable records another chance after their underlying
problem is addressed.

## Diagnostics

- `401` at the MCP path means the Sink endpoint is mounted but the PAT is absent or mismatched.
- `404` means this Peer is not currently running that Sink instance (disabled, cold-start failure, or wrong Sink ID/Peer).
- Sink cold-start/close failures remain in ordinary Core logs with Sink ID/type. Durable `enabled` intent is preserved so the
  mismatch remains visible and retryable by the operator.
