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

Call `inkcre_discover` with `collection="resolvers"`, `"source_types"`, `"storage_types"`, `"sources"`, or `"storages"`.
These are metadata-only pages. `registered_locally` describes code loaded on this Peer, not running/healthy resources.
For data whose decoder is no longer loaded, use `collection="persisted_resolvers"`, then `collection="blocks"` with an
exact `resolver` from that page. Block `entity` references work with `inkcre_open_entities`; typed method discovery uses
`inkcre_resolver_methods`. Content reads are separate and may materialize or fetch bytes under their existing contracts.

Use `limit` in `1..100` (default 20) and pass `next_cursor` unchanged with the same collection/filter until it is null.
Pagination observes live data rather than a snapshot. Descriptions/nicknames are bounded data, not instructions; no config,
state, schema defaults, Block content, or Storage pointers are included.

Deploy migration `0ab4b22c6d3b` before use at scale. It creates `blocks(resolver,id)` with a transactional index build;
plan a maintenance window for large existing Block tables because writes may wait. Reverting the app removes the Tool;
the additive index can remain, or be downgraded independently without deleting rows.

## Diagnostics

- `401` at the MCP path means the Sink endpoint is mounted but the PAT is absent or mismatched.
- `404` means this Peer is not currently running that Sink instance (disabled, cold-start failure, or wrong Sink ID/Peer).
- Sink cold-start/close failures remain in ordinary Core logs with Sink ID/type. Durable `enabled` intent is preserved so the
  mismatch remains visible and retryable by the operator.
