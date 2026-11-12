# MCP Sink Acceptance

> Accepted exact acceptance realization under D-398、D-400 and D-402。This is task control，not an
> automated-test mandate。Tool naming and the discovery/query extension below follow the current
> [#185 MVP baseline](https://github.com/InKCre/impl-py/issues/185#issuecomment-6790532701)。

## Evidence Authorities

- **Deterministic contract**: independent MCP client against a real mounted Streamable-HTTP endpoint and dedicated
  local/preview info-base corpus。
- **Product behavior**: manual ChatGPT Developer Mode journey through Secure MCP Tunnel and the same endpoint。
- **Runtime behavior**: observable Sink/Extension enable-disable-reenable sequence in one real process。
- **Static gates**: repository-declared type、lint、migration and build checks。They support but do not replace the black-box
  journeys。

## Corpus

Create through ordinary producer/Manager and maintenance paths：

1. bounded text/structured Block；
2. storage-backed image or audio Block；
3. oversized text/file Block；
4. a small directed graph with one alternate valid path；
5. an existing Extension-delivered Resolver Block from real public content；
6. ordinary lexical and semantic records for the relevant Blocks/Relations。

No test-only Resolver method、fake retrieval row or corpus-sensitive production branch is admitted。Exact public material is
selected during preflight and recorded with the manual run evidence。

## Journey A — Sink Lifecycle and Protocol Publication

1. Persist one MCPSink with a real PAT and empty `enabled` list：its endpoint is absent。
2. Enable it for the current Peer：`/sinks/{id}/mcp` initializes through authenticated Streamable HTTP。
3. Initialization advertises Tools、Resources and the exact Skills extension subset；`tools/list` exposes exactly eight
   unprefixed Tools：`meta_discover`、`query`、`open_entities`、`read_blocks`、`expand_entities`、`find_path`、
   `resolver_methods`、`invoke_resolver_methods`，with no old-name aliases。
4. Disable the Sink：the same authenticated route returns 404 while an ordinary DB-backed Core API remains usable。
5. Re-enable it：a new independent MCP Client initializes and lists the eight Tools at the same URL with the same PAT。
6. Shut down the active Core/Sink lifecycle：the externally contacted endpoint closes within a bounded wait，and ordinary
   transport/Sink diagnostics report no shutdown errors。Observe this before fallback test cleanup can close resources。

The journey observes endpoint presence and ordinary diagnostics；it does not inspect private running maps or session-manager
flags。

## Journey B — Composable Info-base Retrieval

Using only the MCP surface：

1. call `query` with explicit lexical + semantic modes for one nonblank text query；confirm mode-local evidence is preserved，entities are deduplicated
   and no fabricated cross-mode score/rank appears；
2. open a mixed ordered batch of Block/Relation refs，including one missing ref；confirm natural payloads plus one correlated
   error atom without sibling cancellation；
3. expand Block and Relation entities with `context_limit`；confirm returned facts form valid bounded one-hop neighborhoods；
4. find a bounded path；confirm every returned Relation connects adjacent Blocks and hop count is minimal within the accepted
   traversal contract。Do not require one arbitrary path when several equal valid paths exist。

## Journey C — Content Layers and Resource Delivery

1. Read the bounded Block as `raw`，`hydrated` and `solved`；each result preserves the requested layer。
2. Confirm bounded text/JSON reaches the Tool result directly under one deterministic Block-content URI。
3. Read the storage-backed media and oversized Block；confirm Resource links carry known MIME/size hints and
   `resources/read` resolves current authoritative content without a persisted Resource row。
4. Exercise the chosen bounded image/audio projection through the real MCP Host path；the model must receive usable media，
   not only opaque base64 text or a Storage pointer。
5. Confirm a solved typed result containing bytes preserves its semantic facts while projecting the bytes through Resource
   content rather than embedding base64 in JSON。

The initial inline text/JSON budget is the preflighted 64 KiB UTF-8 constant。Native image/audio versus Resource-link Host
behavior is exercised through the real Host path；the protocol implementation must keep the choice local to the common
projector so acceptance can select the interoperable branch without redesigning Tool contracts。

## Journey D — Dynamic Resolver Methods

1. Discover methods by Blocks and by exact Resolver IDs；when both are supplied，non-empty Blocks take precedence。
2. Multiple Blocks governed by one Resolver produce one catalog result with ordered Block refs rather than duplicate method
   contracts。
3. The catalog includes callable public `get_*` / `read_*` methods with Pydantic-derived input schemas and omits
   `get_existing(db_session)` or any other non-Agent-JSON signature。
4. Invoke at least two independent atoms，one valid and one invalid/unavailable；confirm ordered correlation，isolated error
   and successful sibling completion。
5. The valid Extension Resolver call executes that exact Resolver's ordinary behavior；no MCP-specific Resolver adapter or
   test-only method participates。

## Journey E — Skill and ChatGPT

1. Plugin scan imports exactly one `use-inkcre` Skill through the accepted Skills custom-method/resource subset and verifies
   its SHA-256 manifest。
2. Connect ChatGPT Developer Mode through Secure MCP Tunnel，with the operator companion injecting the MCPSink PAT。
3. Give ChatGPT a real productive task where stored information is relevant without literally requesting a knowledge-base
   search。Observe whether it notices InKCre，retrieves useful evidence and uses that evidence as context rather than treating
   Tool output as a generated answer。

This journey is recorded manually。Its exact Tool sequence，wording and model choice are observations，not stable assertions。

## Journey F — Monotonic Extension Publication

1. Load an Extension that registers a Resolver and exposes an active runtime effect。
2. Disable the Extension：the active effect closes，while the exact Resolver type remains registered and discoverable in the
   same process。
3. Re-enable without re-import rollback；a version change remains a process-restart boundary。

This proves the cross-repository runtime correction through behavior。Do not add registry snapshot/restore unit tests or
assertions tied to `sys.modules` implementation details。

## Journey G — Metadata → Exact Query → Open → Read

Through the mounted Streamable HTTP endpoint and PAT：

1. Discover `resolvers`、`source_types`、`storage_types` and `storages` with `meta_discover`；Resolver identities come only
   from this Peer's registry，and Storage `id/type/nullable nickname` identify a query key，not instance configuration。
2. Use a discovered Storage ID in `query(filters.storage.eq)`；also exercise exact `resolver.eq` and AND combinations。
   Without text，omit retrieval modes；use bounded `id ASC` pages and roundtrip cursors with unchanged filters。
3. Confirm pages have no duplicate IDs，include an unindexed Block，and emit no fabricated relevance score/rank。
4. Feed returned `block:<id>` refs to `open_entities`，then IDs to `read_blocks`，and read the producer's known content。
5. Query a known historical Resolver ID absent from the catalog；persisted data remains discoverable without implying that
   the local Peer can execute its Resolver methods。
6. For lexical and semantic text queries，prepare more than K higher-ranked nonmatching Blocks and a high-score Relation。
   Assert the filtered query still returns the expected matching Block IDs/count，proving predicates precede top-K。

Use normal graph/Storage production and owning Manager maintenance paths；a deterministic graph-blind embedding dialect is
allowed，but mocked retrieval Managers or direct SQL index fixtures do not establish this journey。Retain separate SQL
whitelist/read-only/handler-isolation evidence；metadata and pure-condition reads do not load secrets or materialize content。

## Completion

The unit passes only when Journeys A–D、F and G have deterministic evidence，Journey E has recorded manual product evidence，the
repository gates pass，and the relevant preview/deployment path is healthy。No automated ChatGPT assertion or literal/schema
mirror test is required。
