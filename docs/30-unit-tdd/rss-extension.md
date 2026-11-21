# RSS Extension

## Purpose

本文记录 core-py `rss` extension 的稳定本地架构。它保留 RSS 与 Atom 的 source type identity，内部共享
一个 feed collection vertical：

```text
source schedule/manual command
  -> pending collect job
  -> bounded HTTP + feedparser adapter
  -> exact feed/item reconciliation
  -> feed/item/enclosure graph
  -> resolver use projection
  -> scoped source state
  -> optional full-text/enclosure materialization
```

Hub Product TDD 只拥有 generic collection、graph、storage、resolver、effect vocabulary 与 authority 合同；
本文拥有完整的 RSS/Atom-specific product/technical contract，包括 Python package、exact IDs、config/state shape、
relation grammar、事务与 acceptance 边界。

## Delivery Boundary

- 支持 RSS 2.0 与 Atom feed；旧 source type import paths 保持不变。
- 每个 source instance 配置一个 HTTP(S) `feed_url`。
- full-text enrichment 默认开启；automatic enclosure download 默认关闭。
- 提供手动 `POST /rss/enclosures/materialize` command。
- 不提供 feed-reader UI、OPML、所有 malformed feed 容错或 organization/retrieval 产品。

## Package Topology

```text
extensions/rss/
  rss.py + atom.py        durable source type wrappers
  source.py               shared SourceBase behavior
  schema.py               source config/state and canonical values
  http.py                 bounded conditional HTTP transport
  adapter.py              feedparser -> canonical snapshot
  service.py              source policy and command orchestration
  reconcile.py            graph reconciliation through required graph UoW
  resolver.py             feed/item/enclosure use projections
  enrichment.py           full text and enclosure materialization
  api.py                  explicit materialization command
```

`rss.py` 与 `atom.py` 只固定 protocol family 和 source type identity；parser、policy、graph mechanics 不复制。
Transport 不使用 parser 自带 downloader；HTTP timeout、redirect、conditional headers 和 byte limits 由本 unit
显式拥有。

## Source Config, Job Config, And State

`FeedSourceConfig` 拒绝 unknown fields，主要字段包括：

- feed transport：`feed_url`、request timeout、8 MiB feed cap、user agent；
- full text：`fetch_full_text=true`、8 MiB article cap；
- enclosure：`download_enclosures=false`、64 MiB cap、`target_storage_id=-4`；
- unidentified item：`create`（默认）或 `discard`。

Job config 只允许覆盖 `fetch_full_text`、`download_enclosures` 和 `target_storage_id`。旧 `full` 参数不再接受，
因为它曾把扫描范围、增量 cutoff、顺序和分页等不同 effect 混成一个 boolean。

`FeedSourceState` 保存：

- `etag` / `last_modified`，scope 是 `snapshot_configured_url`；
- `last_successful_contentful_snapshot_observed_at`，scope 是 `snapshot_feed_block_id`。

Configured URL 改变时不能重用旧 conditional validators；exact feed root 改变时不能重用旧 unidentified-item
watermark。`304` 可以更新 validators，但不推进 contentful watermark。

## Collect-Job Lifecycle

Manual command 与 Cron firing 都创建 `core.source.collect.v1` `PENDING` Job。`JobManager.run()` 先通过本地
Source Job handler 验证 eligibility，再用 conditional update 原子 claim `PENDING -> RUNNING`；只有 claimant 执行
Source。完成后从仍为 `RUNNING` 的 row 关闭为
`FINISHED` 或 `FAILED`，timeout/重复 runner 不会复活已经关闭的 execution。

Cron 的职责只是创建 typed Job；它不是第二条直接调用 `source.collect()` 的 effect path。Pending-job scanner、
manual Job 与 Cron-created Job 使用同一个 runner and diagnostics authority。

## Canonical Graph And Exact IDs

Exact resolver contract versions：

- `extensions.rss.feed.v1`
- `extensions.rss.feed_item.v1`
- `extensions.rss.enclosure.v1`

Relation grammar：

| From | To | `relation.content` | Meaning |
| --- | --- | --- | --- |
| feed item | feed | `feed` | exact feed membership / identity scope |
| feed item | enclosure metadata | `enclosure` | protocol-authored enclosure component |
| feed item | `core.text.v1` | `full_text` | derived main-text enrichment |
| enclosure metadata | semantic content | `content` | downloaded actual content |

Feed、item 与 enclosure inline content 只保存各自 protocol/source-authored canonical facts。Feed membership、
enclosures、full text 与 materialized bytes 不复制进 root content。Enclosure order 未提升，因为当前 use 无此需求。

## Identity And Reconciliation

Feed identity 是 source-instance-scoped ladder：

1. protocol-native feed ID；
2. declared self URL；
3. configured URL。

Exact match 更新同一 feed block。若 configured URL 或 native facts 无法证明 continuity，则创建新 feed root；
历史 feed/items 保留。

Item identity：

1. Atom ID 或 scoped RSS GUID；
2. alternate link；
3. no exact identity。

Exact match 才 update/idempotent；不使用 title/content fingerprint 或 fuzzy overwrite。无 ID/link 时：

- `discard`：跳过并记录 diagnostic；
- `create`：默认创建；若 item 有可信 authored time 且不晚于该 feed 上次 successful contentful snapshot，
  作为 admission heuristic 跳过。

Watermark 只是减少 duplicate 的 cutoff，不是 identity 或 reconciliation proof，也不允许按 document order
提前停止扫描。

## Persistence And Failure Boundary

FeedGraphReconciler 使用异步 Source/Graph UoW，SQL 由 Core persistence 执行。
Feed root reconciliation 对 source row 加锁。每个 admitted item 使用自己的 primary transaction 完成 item root、
feed relation 与 enclosure metadata graph；同一 snapshot 的 items 不承诺一个大事务。

- exact replay 返回 unchanged；canonical content 变化更新 block，并删除 stale embedding。
- primary item failure 记录 diagnostic、保留已完成的前序 graph，并使 job failed；source state 不推进。
- retry 通过 exact identity reconcile 已有 residue，不要求 compensation/audit/replay subsystem。
- enrichment failure 只进入 structured diagnostics，不改变已经成功的 primary collection。

## Full-Text Enrichment

Trafilatura 只处理本 unit bounded HTTP client 已取得的 HTML；它不拥有 downloader、storage 或 identity。
Full text 是独立 inline `core.text.v1` block，通过 `full_text` relation 连接 item。

- collection 默认尝试 materialize；unchanged item 不因每次 run 无条件重抓。
- alternate URL 改变时以 `refresh` 更新 existing full-text child，并清除 stale embedding。
- resolver use projection 优先 full text，其次 feed-authored content、summary、title。
- 无 URL、提取为空或抓取失败时 capability unavailable/diagnostic，不伪造文本。

`FeedItemResolver.get_solved_content(materialize_missing=True)` 可以 lazy materialize missing full text；这是
resolver capability 中显式允许的 read-triggered write，不是隐藏的 storage read side effect。

## Enclosure Materialization

Enclosure metadata 始终入图。Automatic policy 和 manual API 都调用 resolver instance 上的同一个
materialization command；input 是 enclosure block IDs，不是 URL DTO。

Evidence order 属于 protocol adapter：

- RSS：declared MIME → HTTP observed MIME → filename → byte signature；
- Atom：HTTP observed MIME → declared MIME → filename → byte signature。

每一项交给 `ResolverManager.match_media_type()` 做 exact core resolver match；全部失败显式 fallback
`core.file.v1`。Manager 只拥有 common match，不拥有 RSS/Atom evidence ladder。

物化使用配置的 `WritableStorage.prepare_write()`。外部存储先上传 bytes，PostgreSQL 在图事务内写入 bytes。
写入结果是一个 exact semantic content block，enclosure metadata 通过唯一 `content` relation 指向它。
Row lock 保证并发物化最多形成一个 child，重复命令返回已存在的 child。

Manual API 对每个 enclosure 返回独立 result/error，不把一个失败扩散成 batch-level rollback。

## Resolver Use Boundary

Feed/item/enclosure resolver 联合 inline canonical content 与 outgoing direct relations形成 solved projection。
`include_in/include_out` 始终相对 subject block；不是 recursive graph traversal。

`refresh` 只替换 block hydration、relations 或 solved content 的 local snapshot；`materialize_missing` 只允许
创建 absent derivation。代码不得使用 `force`/`reload` 表达这些不同 effect。

## Verification Authority

- actual RSS/Atom HTTP doubles：conditional request、effective URL、relative enclosure URL、fatal family mismatch；
- PostgreSQL integration：feed/item/enclosure graph、exact replay/update/new、identity config change、watermark、
  partial residue 与 retry；
- job acceptance：manual/scheduled ordinary job、atomic claim、legacy config rejection；
- enrichment：real HTML extraction、image/audio/video/PDF/EPUB/ZIP/file bytes、MIME conflict ladder、concurrent
  materialization；
- optional live smoke：由 `INKCRE_LIVE_RSS_URL` / `INKCRE_LIVE_ATOM_URL` 显式选择 endpoint。

测试资产由 repository-owned generator 按需生成并由 Git 忽略；case table 与生成器是 source artifacts，
generated media 不是 durable repository data。
