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

MVP 暴露且只暴露：

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
