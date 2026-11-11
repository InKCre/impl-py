# MCP Sink

本文拥有 core-py 的 persisted Sink lifecycle 与 `core.mcp.v1` 实现合同。共享 Sink 产品语义不在此重定义。

## Topology

```text
Extension --register type--> SinkManager <--persisted sinks / Peer enable intent
                                |
                                +--> SinkBase instance --active resource--> MCPSink endpoint

MCP Tool --> existing retrieval / graph / info-base Manager --> Block/Relation/Resolver/Storage authority
```

`sink_types` 保存 exact type description/config schema；`sinks` 保存 instance nickname/config 与 `enabled uuid[]`。
没有 generic Sink state、transport、protocol、path 或 runtime-status column。Runtime presence 只在当前进程内存在。

Registration 与 execution 分离：Sink class import 只登记 type；显式 instance enable 或 cold-start intent 才构造
`SinkBase`。Config 可在 disabled 时更新；running instance 接收已经由 type schema 验证的完整新 config。Delete 要求
所有 Peer intent 为空且本地没有 running instance。

## MCP Protocol Projection

一个 running MCPSink 拥有一个官方 SDK server/session manager 与一个 exact FastAPI mount。子应用使用 stateless
Streamable HTTP JSON；parent lifecycle 显式进入 SDK session manager，并在 close 时按 route identity 撤销 mount。
Public Host authority 属于现有 deployment/reverse proxy；SDK 的独立-listener Host filter 在嵌入模式关闭。Endpoint
仍要求该 instance config 中长期有效的 Bearer PAT，不使用 Peer JWT。

当前暴露八个通用 Agent actions：

- `inkcre_discover`
- `inkcre_recall`
- `inkcre_open_entities`
- `inkcre_read_blocks`
- `inkcre_expand_entities`
- `inkcre_find_path`
- `inkcre_resolver_methods`
- `inkcre_invoke_resolver_methods`

Batch 返回按输入关联；自然 payload 表示成功，仅失败 atom 包含浅层 error。Recall 的 lexical/semantic mode 独立执行，
按 entity ref 去重并保留 mode-local evidence，不合成 cross-mode score。

Recall 候选的 `label` 帮助调用者判断读取对象：lexical 沿用索引名称，semantic Block 使用既有
Resolver label，Relation 展示有界关系文字和端点引用。名称不可用时为 null，仍保留实体和 score。
名称和 excerpt 是来源内容，不是指令；后续读取继续使用现有 Tool/Resource，不增加结果版本或窗口协议。

## Metadata Discovery

`inkcre_discover(collection, limit=20, cursor=null, resolver=null)` 提供从零开始的只读发现。
返回 `{collection, results, next_cursor}`；没有结果时为空列表和 null cursor。

| collection | authority / 字段白名单 |
| --- | --- |
| `resolvers` | 当前 Peer registry：`id`、description、`registered_locally=true`、`supports_draft`；具体 typed read capabilities 继续用 `inkcre_resolver_methods` |
| `source_types` | 持久 catalog：`id`、description、`registered_locally` |
| `storage_types` | 持久 catalog：`id`、description、`writable`、`registered_locally` |
| `sources` | 配置实例：`id`、`type`、nickname、`storage`、`block`、`registered_locally`；不创建缺失 anchor |
| `storages` | 配置实例：`id`、`type`、nickname、`registered_locally`，保留负数内置 ID |
| `blocks` | exact resolver 下的持久引用：`id`、`entity=block:<id>`、`resolver`、`storage`、row timestamps、`registered_locally` |

`registered_locally` 只表示本 Peer 已加载 class，不表示 Extension enabled、instance running、配置有效、外部资源
可达或执行会成功。Source/Storage catalog 不按本地 registry 过滤；Resolver 目录唯一来自 MCP Sink 所在 Peer 的
`ResolverManager.RESOLVER_CLS`，不访问数据库，也不从持久 Block 值派生另一份目录。
`blocks` 必须提供已知的非空 exact `resolver`，不要求其在 registry 中；其他 collection 拒绝 resolver 参数。
历史未注册 Resolver 的 Block 仍可通过 entity preview / persisted content 读取，solved/method reads 则明确 unavailable。

分页始终为 `1..100` 项，默认 20；按唯一 ID 升序、exclusive keyset seek，数据库只读 `limit+1` 项白名单列。
字符串排序/比较沿用数据库同一 collation；内存 registry 按 exact ID 排序。opaque cursor 带版本、collection、
exact resolver filter 与有类型的最后返回 key；畸形、错误类型或跨 scope cursor 报参数错误，不退回第一页。
这是 live traversal，不提供跨调用快照、total count 或并发修改下无遗漏保证；cursor 不是授权凭据。

description 最多 512 字符，nickname 最多 160 字符，均是描述数据而非指令；exact ID 不截断。
config、state、所有 config schemas、Block content/pointer、blob bytes 从 SQL 列投影中排除。
发现不构造 Resolver/Source/Storage handler、不动态 import、不调用 get_label、hydrate、解析、AI 或外部读取，也不写入数据。
持久数据读取使用 owning domain 的短 UoW；Resolver 目录只读本地内存 registry，不保留跨调用 session/cache。

`blocks(resolver,id)` 索引支持已知 exact Resolver 下的 Block keyset seek。
部署新增索引使用普通 transactional build，期间可能阻塞 Block 写入；
大库应安排维护窗口。降级只删除该索引，不删除业务数据。

## Content And Resolver Methods

`inkcre_read_blocks` 每次调用统一选择 `raw | hydrated | solved`。Common projector 保留 Pydantic/dataclass/container
shape；nested bytes 变为 Resource descriptor。UTF-8 text/JSON 在 64 KiB 内可 embedded，超过预算或 bytes 返回 live
Resource link。URI 编码 Block、content layer、method arguments 与 value selector；`resources/read` 重新执行普通
Block/Storage/Resolver read，不持久化 Resource 行。

动态 Resolver behavior 不展开成无限顶层 Tools。`inkcre_resolver_methods` 从 Blocks 优先、否则 exact Resolver IDs
选择当前 registry，并把同 Resolver Blocks 归组。只有 public typed `get_*` / `read_*` 且 non-self input 可形成
Pydantic JSON Schema 的 method 被列出；untyped、variadic、runtime-dependent contract 被忽略。
`inkcre_invoke_resolver_methods` 再按 Block 的 exact Resolver 验证并调用 ordinary bound method；每个 atom 独立，结果
使用同一个 projector/Resource boundary。

## Skill

Server 实现 OpenAI 当前使用的 bounded `io.modelcontextprotocol/skills` extension：`skills/list`、`skills/get` 与
一个静态 `skill://inkcre/use-inkcre/SKILL.md` Resource。Skill 只建立 Block/Relation/Resolver/Storage 心智模型与
“已收集信息可能改善当前生产/创造工作”的元认知，不规定固定 Tool recipe。
