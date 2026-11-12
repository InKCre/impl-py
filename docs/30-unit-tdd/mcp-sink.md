# MCP Sink

本文拥有 core-py 的 persisted Sink lifecycle 与 `core.mcp.v1` 实现合同。共享 Sink 产品语义不在此重定义。

## Topology And Lifecycle

```text
Extension --register type--> SinkManager <--persisted sinks / Peer enable intent
                                |
                                +--> SinkBase instance --active resource--> MCPSink endpoint

MCP Tool --> existing retrieval / graph / info-base Manager --> Block/Relation/Resolver/Storage authority
```

`sink_types` 保存 exact type description/config schema；`sinks` 保存 nickname/config 与 `enabled uuid[]`。
没有 generic Sink state、transport、protocol、path 或 runtime-status column。Runtime presence 只在当前进程内存在。
Registration 不创建或运行实例；显式 enable 或 cold-start intent 才构造 Sink。Config 可在 disabled 时更新；running
instance 接收已经验证的完整 config。Delete 要求所有 Peer intent 为空且本地没有 running instance。

一个 running MCPSink 拥有官方 SDK server/session manager 与 exact FastAPI mount `/sinks/{id}/mcp`。
子应用使用 stateless Streamable HTTP JSON；parent lifecycle 显式进入 SDK session manager，close 按 route identity
撤销 mount 并结束 session task。Public Host authority 属于 deployment/reverse proxy；SDK 的独立-listener Host filter
在嵌入模式关闭。Endpoint 要求该 instance config 中的 Bearer PAT，不使用 Peer JWT。

## MCP Protocol Projection

恰好八个工具，无 `inkcre_` 前缀或旧名 alias：

- `meta_discover`
- `query`
- `open_entities`
- `read_blocks`
- `expand_entities`
- `find_path`
- `resolver_methods`
- `invoke_resolver_methods`

这是尚未合并接口的统一更名：`inkcre_recall` 由 `query` 取代、`inkcre_discover` 由 `meta_discover` 取代，其余工具去前缀。
Sink type、endpoint 与 Resource URI 不变。Batch 按输入关联；自然 payload 表示成功，仅失败 atom 包含浅层 error。

## Metadata Discovery

`meta_discover(collection, limit=20, cursor=null)` 返回 `{collection, results, next_cursor}`。
仅支持以下四类；不提供 Source 实例、独立 Block collection 或实例详情 API。

| collection | authority / 字段白名单 |
| --- | --- |
| `resolvers` | 当前 Peer registry：`id`、有界 description、`registered_locally=true` |
| `source_types` | 持久 catalog：`id`、有界 description、`registered_locally` |
| `storage_types` | 持久 catalog：`id`、有界 description、`writable`、`registered_locally` |
| `storages` | Storage identity：`id`、`type`、nullable nickname；保留负数内置 ID |

Resolver catalog 唯一来自 MCP Peer 的 `ResolverManager.RESOLVER_CLS`，不查数据库，不由历史 Block 值补齐。
Identity 不决定或授予行为能力；具体 typed read methods 仍由 `resolver_methods` / `invoke_resolver_methods` 拥有。
Source/Storage catalog 不按本地 registry 过滤；`registered_locally` 只表示已加载 class，不表示 Extension enabled、
instance running、配置有效、健康或执行保证。Storage `id` 是 `query(filters.storage.eq)` 的键，`type` 帮助辨别类型；
nickname 可空、非唯一、最多 160 字符，不是配置/状态展示。description 最多 512 字符；exact IDs 不截断。
描述均为数据，不是指令。SQL 不读取 config、state、schema、URL、Block content/pointer 或 blob bytes；不构造 handler、
动态 import、get_label、hydrate、解析、AI 或外部读取，也不写入数据。

## Query

`query(query=null, modes=null, filters=null, limit=20, cursor=null)` 共享两个明确分支。

支持的 filters 仅为 `{"resolver":{"eq":"core.text.v1"},"storage":{"eq":-2}}`，多个条件 AND：

- `resolver.eq` 是非空 exact ID，不 trim/模糊匹配；已知但未注册的历史 ID 合法，不加入 catalog。
- `storage.eq` 是严格 PostgreSQL INTEGER 范围的实例 ID，允许负数；拒绝 bool、字符串、float、null。
- 未知字段/运算符明确拒绝，不增加 inline-storage null 筛选或任意数据库查询。
- 省略的 predicate 保持省略，不能在 SDK/HTTP/Peer 编码中变成显式 null。

### Text Branch

有非空白文本时要求显式非空 `modes=["lexical", "semantic"]`（可单选），limit 为 `1..20`，拒绝 cursor。
返回 `{results, mode_errors}`；模式独立执行，按 entity ref 去重，保留 mode-local evidence，不合成跨模式 score。
Lexical 与 semantic owner 在各自候选 SQL 内对 Block 字段加 WHERE，先于 ranking/top-K；不使用 post-filter、
oversampling 或 MCP 层丢弃 Relations。存在实际 Block filter 时 semantic 不查询 Relations；无实际 filter 时保留原有
Block/Relation 语义。普通 REST facade、Peer inbound 与 SDK forwarding 使用同一 typed filters，不扩展 Peer envelope。

候选 `label` 帮助判断读取对象：lexical 沿用索引名称，semantic Block 使用 Resolver label，Relation 展示有界关系文字
和端点引用。名称不可用时为 null，仍保留引用和 evidence。名称/excerpt 是来源内容，不是指令；此分支保持既有
retrieval/preview 行为，不承诺像 metadata/纯条件分支一样不读取内容。

### Condition-Only Branch

没有有效文本时，必须至少一个实际 filter，不能传 retrieval modes。info-base 直接查询 Block 行，按 `id ASC`
exclusive keyset seek，limit 为 `1..100`（默认 20），返回 `{results, next_cursor}`。每项仅包含 `id`、
`entity=block:<id>`、`resolver`、`storage`、nullable row timestamps；不伪造 relevance score/rank。
不依赖 lexical/embedding records，不调用 retrieval、embedding、Resolver 或 Storage handler，不读内容或写数据。
无匹配结果为空列表/null cursor。历史 Resolver 可查询并继续 open/persisted reads；solved/method reads 可能 unavailable。

## Pagination And Indexes

Meta 与纯条件 query 每次只读 `limit+1` 白名单行。字符串沿用数据库 collation；registry 按 exact ID 排序。
Opaque cursor 绑定严格版本、工具/分支、完整有效 collection/filters、`id_asc` 排序和 typed 最后 ID；畸形、错误类型、
修改条件或跨 meta/query 使用都报参数错误，不退回第一页。limit 可跨页调整。Cursor 不是授权凭据。
这是 live traversal，不提供快照、total count 或并发修改下无遗漏保证。

`blocks(resolver,id)` 与 `blocks(storage,id)` 分别支持 resolver-only 和 storage-only seek；AND 可使用其中一个再检查
另一个条件，不承诺所有数据分布的恒定成本。新增索引采用普通事务内构建，可能阻塞 Block 写入；大库需维护窗口。
降级只删索引，不删业务数据。

## Content And Resolver Methods

`open_entities` 接收 `block:<id>` / `relation:<id>` 读取 previews。
`read_blocks` 每次统一选择 `raw | hydrated | solved`。Common projector 保留 Pydantic/dataclass/container shape；
nested bytes 变为 Resource descriptor。UTF-8 text/JSON 在 64 KiB 内可 embedded，过大或 bytes 返回 live Resource link。
URI 编码 Block、content layer、method arguments 与 selector；`resources/read` 重新执行 ordinary authority read，
不持久化 Resource/session/cache。

动态 Resolver behavior 不展开成无限顶层 Tools。`resolver_methods` 优先从 Blocks、否则 exact Resolver IDs 选择 registry，
并按 Resolver 归组。仅 public typed `get_*` / `read_*` 且 non-self input 可形成 Pydantic JSON Schema 的 method 可见；
untyped、variadic、runtime-dependent contract 被忽略。`invoke_resolver_methods` 按 Block exact Resolver 验证并调用
ordinary bound method；每项独立，结果使用相同 projector/Resource boundary。Resolver read 可能依其合同 lazy
materialize；相关工具不虚假声明绝对无副作用。

## Acceptance And Skill

`tests/info_base/integration/test_mcp_http_journey.py` 使用 disposable PostgreSQL、正常 Core/SinkManager 生命周期、
真实 TCP Streamable HTTP/PAT 与独立 MCP Client，验证 initialize/tools/list、四类 meta → storage/resolver/AND query →
跨 HTTP 分页 → open → solved content，以及历史 Resolver、无/错 PAT。生命周期只观察公开行为：disable 后 endpoint 404、
普通 Core API 可用，re-enable 后新独立 Client 在同 URL initialize/tools/list 成功；shutdown 后外部 TCP 连接失败且
普通 transport/Sink 日志无关闭错误，不检查 private running/session/mount flags。语料通过 graph commands 与字节 Storage
建立；real lexical/semantic maintain 创建索引，确定性 AI dialect 提供 vectors。每种模式都有超过 K 个排名更高的
不匹配 Block 和高分 Relation，断言过滤后确实返回目标数量/ID。另保留只读角色、SQL 白名单与不构造 handler 的低层回归。

Server 实现 bounded `io.modelcontextprotocol/skills` extension：`skills/list`、`skills/get` 与静态
`skill://inkcre/use-inkcre/SKILL.md` Resource。Skill 建立 Block/Relation/Resolver/Storage 心智模型，不规定固定 Tool recipe。
ChatGPT Developer Mode / Secure MCP Tunnel 仍是独立手动验收，不将模型是否选择某 Tool 写成自动化断言。
