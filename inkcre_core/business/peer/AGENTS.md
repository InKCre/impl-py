# peer/ Local Guide

## Boundary

- `PeerModel` is one equal deployment identity with owner config、a full capability/inbound snapshot and one lease。
- `PeerManager` owns local inbound/outbound registries、self-publication、database-time discovery and one-shot delegation。
- Capability IDs and protocol envelopes are opaque here。Business modules own typed request/response codecs and fixed routes。
- `core.peer.protocol.http.v1` means normalized JSON HTTP + Peer JWT。Only a proven pre-dispatch failure or exact
  `InkCre-Peer-Execution: not-executed` permits failover。

## Guardrails

- Never add a generic capability invoke route or delegation job。
- `labels` do not influence routing；readiness is never advertised。
- `route_to_peer` is a caller-local constraint and never enters payload/advertisement。
- A normal response or outcome-unknown dispatch stops generic failover。
- HTTP absolute URLs come only from owner config + fixed inbound paths；do not infer them from bind host or requests。
- Peer runtimes register with an ordinary `peers` upsert. A default `name` is inserted only for a
  new identity; registration refreshes runtime-owned `application_version` and `config_schema`
  without replacing Human-owned `name`、`config` or `labels`.
