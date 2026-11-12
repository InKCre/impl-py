# sink/ Local Guide

本目录实现 deployment-owned Sink type/instance lifecycle 与具体 Sink projections。跨 Unit authority 见
[business-pipeline-and-authority.md](../../../docs/30-unit-tdd/business-pipeline-and-authority.md)，MCP 具体 contract 见
[mcp-sink.md](../../../docs/30-unit-tdd/mcp-sink.md)。

## Stable Boundaries

- `SinkBase` 注册 exact type 并拥有一个 running instance 的 active resources；`SinkManager` 拥有 catalog、persisted
  instance、Peer-scoped enable intent 与 process-local running map。
- Type registration 不创建或启动 instance。Extension 可以交付 Sink type，但 Extension lifecycle 与 Sink instance
  lifecycle 相互独立。
- Enable/disable 先持久化当前 Peer intent，再 start/close local runtime。失败保持可观察且不反向改写 durable intent。
- 不增加 generic deliver、reconcile、transport、restart 或 rollback interface；具体 Sink 直接实现 `on_start/on_close`。
- Sink 只投影既有 use behavior，不取得 info-base、retrieval、Resolver 或 Storage authority。
- SinkBase hooks 默认 no-op；只有拥有 active resource 的类型实现它们。Agent Query instance 挂载 exact REST
  route，MCP instance 挂载 MCP endpoint，两者都由 SinkManager 的同一实例 lifecycle 启停。

## Agent Query Boundary

- `core.agent-query.v1` config 只选择一个 Agent definition；prompt、model、tools 和 model-call budget 留在 Agent。
- `/sinks/{id}/query` 异步创建 `core.sink.agent-query.v1` Job，不在 HTTP request 内运行模型。
- `submit_query_result` 是 Sink-owned delivery Tool。读取工具仍由 info-base、Resolver 和 Graph Navigation 拥有。
- result 保存在 Job state，不建立额外 query/result table。正常结束但未提交结果是明确失败；已经闭合的提交在
  后续失败或取消时尽力保留。

## MCP Boundary

- `core.mcp.v1` 使用官方 SDK 的 stateless Streamable HTTP，exact endpoint 是 `/sinks/{id}/mcp`；PAT 不复用 Peer JWT。
- Tool surface 为八个通用 Agent actions（无前缀，包括 metadata-only `meta_discover` 与双分支 `query`）。发现只投影 owning domain 的
  bounded 白名单列，不读取 config/state/schema/content，不构造 handler 或触发 materialization。
  Resolver-specific read behavior 通过 scoped method discovery/invocation envelope
  动态投影，Resolver 与 Extension 不声明 MCP metadata。
- `raw`、`hydrated`、`solved` 分别来自 persisted Block、Storage hydration 与 exact Resolver。过大/bytes value 使用
  deterministic live Resource URI；Resource read 重新读取当前 authority，不建立持久 Resource/session/cache。
- Resolver public typed `get_*` / `read_*` method 才可投影；runtime dependency、untyped 或不可形成 JSON Schema 的
  signature 被忽略。Tool runtime/Pydantic 是唯一 input validation owner。
- MCP read-only 是 Agent intent boundary。Resolver read 可按既有 contract lazy materialize，因此不能把所有 Tool
  标注成绝对无 state change。
