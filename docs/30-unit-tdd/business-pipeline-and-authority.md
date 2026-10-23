# Business Pipeline And Authority

Shared Product and cross-unit contracts are owned by `../_shared/10-prd/` and
`../_shared/20-product-tdd/`. This document owns only core-py's internal unit boundaries and
implementation direction; it must not redefine Peer wire behavior or shared capability semantics.

## Purpose

本文件记录 `core-py` 内部一条慢变量结构：

`extension -> source/protocol + resolver/storage -> info_base -> application/use`

它不是局部目录 hazard，而是跨多个 business subtree 的 unit-local architecture。

## 何时阅读

在以下情况先读这里：

- 修改 `app/business/extension/`
- 修改 `app/business/source/`
- 修改 `app/business/info_base/`
- 修改 `app/business/ai/` 或 application/use capability
- 修改这些子树之间的 ownership / dependency / authority

## Structure

### 1. Extension Is The Runtime Expansion Entry

- extension 是 runtime 扩展入口。
- extension startup 会发布 extension-owned API route、Peer inbound、public claim，并登记其 source、resolver、sink
  等 Python capability types。close/disable 只撤销 exact active effects；已 import 的 type registration 在当前进程
  单调保留，exact package version replacement 以 restart 为边界。
- extension API 默认继承 core peer JWT dependency；需要 public 或 external-protocol auth 的
  extension 通过 `api_dependencies()` 显式使用 auth-neutral root，再在自己的 child routers 上组合
  public / self-auth dependencies。
- extension config update 的顺序是 merge complete next value → typed validation → durable write →
  live assignment。disabled extension 也可先保存有效配置，下一次 enable 时加载。
- installed extension 的 persisted block decoder 不随 enable/disable 消失；运行 API/source 和读取
  已持久信息是不同 lifetime。
- extension 自己不拥有 core graph persistence，也不自动拥有 retrieval semantics。

### 2. Source And Protocol Adapters Produce Collection Commands

- source 负责从外部世界采集或记录输入。
- extension-owned protocol adapter 也可以接收 external client input；Memos backend 是首个已验证
  例子，它不是 `SourceBase` instance，也不因此获得单独的 memo object store。
- source 或 protocol adapter 负责 native shape 与 extension-owned canonical command 之间的映射，
  但不是 persistence owner。

### 3. Info-Base Owns Graph Persistence And Cross-Retrieval Reads

- `InfoBaseManager` 协调 graph-form normalization、producer graph command，以及不属于某一种 retrieval
  实现的跨模式检索和实体读取。`commands.py` 和 `services.py` 拥有具体 graph 用例与 Block/Relation CRUD。
- producer 可以提出 recursive `StarsGraphForm` 或 flat signed-ID `GraphForm`；normalization、block/relation insert 与
  database-managed identity 由 info-base 协调。
- 多个 graph mutation 通过必需的 GraphUnitOfWork 组合；只有 persistence 层接触 raw session。
- persistence helper 的 transaction 边界不自动成为产品级 graph-completeness guarantee；具体 command
  的 primary effect、partial result 与 cleanup 语义由 owning unit 声明。

### 数据库事务边界

`app/business` 拥有应用用例、业务规则及事务组合；`app/persistence` 按业务责任分组，拥有
session-bound repository 和 UoW 的数据库工厂实现。用例决定何时进入、退出作用域，UoW 工厂负责
实际创建 session 和原生事务 framing。repository 不反向依赖 business。
HTTP route 只解析请求并调用 business 入口，不直接查询 repository；例如 `get_entity_records`
统一读取两类实体，route 在作用域结束后完成内容表示与 HTTP 响应映射。


`/graph` 使用 `info_base.commands.submit_graph`：应用用例打开 `graph_uow()`，原生 SQLAlchemy
作用域在成功退出时提交、失败时回滚并关闭 session。`persist_graph(graph, uow)` 和
`persist_stars(graph, uow)` 是供组合用例调用的事务内操作，不结束事务。GraphUnitOfWork 中的
BlockRepository、RelationRepository 和 StorageRepository 绑定同一个 session，因此图和 blob 可以
共同提交或回滚。repository 只执行 SQL 及必要的 flush，不创建 session，不提交或回滚。

Graph 的负 local ID 只用于结果映射，不能作为数据库 ID 写入。flat graph 批量 flush 不承诺固定的
数据库网络往返次数；Stars 则保留每个 Resolver 的精确身份协调。`get_existing_async(blocks)` 接收
绑定事务的 BlockRepository，默认匹配 resolver/content；GitHub 按 node_id 匹配并拒绝歧义。

BlockService 和 RelationService 提供独立异步 CRUD；HTTP 批量实体查询在一个短 scope 中完成，随后
才加载内容。Resolver 的 storage catalog、relation、配置读取和派生写入已异步化。materialization 在
AI 调用前释放读取 scope，返回后重新检查已有派生，再在一个 scope 中插入图。该检查保持原有 best-effort
语义，不构成跨并发调用的唯一性承诺。

配置 HTTP 使用 DeploymentConfigService，AI、Agent 定义和 Peer 的数据库入口各自使用所属 UoW。
AI provider 调用和 Peer outbound 执行前结束数据库作用域；Peer 的内存 capability 注册仍是同步函数。
`app/engine.py` 是异步 factory 的 authority，lifespan 在业务运行资源退出后 dispose 异步 engine。
UoW 不跨并发任务共享。应用操作不接受可选 session；共同原子提交必须组合事务内操作，而不调用
独立提交入口。`expire_on_commit=False` 允许已提交的已加载字段在 scope 外读取，不授权隐式延迟查询。

ExtensionStateService 与 Host 的数据库操作已异步化，state/config transform 在行锁内同步执行，
SQL 位于 app/persistence/extension；Source catalog 批量同步提供 SDK async startup 所需能力。
启用／停用的持久化失败补偿和启动取消清理属于 Host；Peer 广播失败不撤销已提交的 disabled 状态。
Registry origin 查询结束后才执行网络／wheel 获取。旧同步 Host 合同通过 Host 0.2 窗口隔离。

Source 配置／状态、Sink catalog／intent、Job admission／claim／close、Cron occurrence 都使用异步
persistence。Job handler 和外部 Sink lifecycle 在提交后的作用域外运行。Cron 创建 Job 与推进 occurrence
共享 CronUnitOfWork，任一步失败回滚完整用例；不同 Cron 独立处理。Job 的取消／超时收尾另开短事务，
只更新仍为 RUNNING 的记录，并限制清理等待时间。执行资格检查的 Source、Agent、AI 与配置读取也可 await。

GitHub、RSS、Mail、Telegram、Twitter 和 Memos 的采集与物化已异步化。扩展的协调代码通过必需的
Graph/Source UoW 组合 Core persistence，不创建 session。Twitter page graph 与 Source cursor 同事务；
RSS/Mail 保持逐 item/occurrence 的既有 partial effects，Memos 主删除后的 best-effort cleanup 仍独立提交。

Lexical、Semantic 的 SQL 与批量 upsert 归各自 persistence；候选读取、Resolver/AI 计算、
结果写入分为不同作用域。Graph navigation、Organization、MCP 与 Agent Tool 原生 await 业务入口。
组织行为的多个图写入同事务，外部 Agent 执行不持有 session。并发 Tool 各自打开 UoW。

运行时不存在旧同步 SessionLocal、可选 session CRUD 或双轨 Storage API。同步测试数据准备只在
`tests/database.py` 中，通过独立 NullPool engine 直接插入／读取 fixture，不复制业务身份协调逻辑；
业务验收调用生产异步入口。Alembic／CLI 与 readiness 共用的 `app/database_contract` 保留独立同步
连接，HTTP readiness 在线程中执行，详见 Runtime Orchestration。日志使用独立异步 batch 事务。

`pdm run lint:database-boundaries` 是 `pdm run check` 的组成部分：`ruff.database.toml` 在全部
runtime 路径禁止同步 session、scoped session 和直接驱动连接；`scripts/check_database_boundaries.py`
检查 engine/factory 归属、route 到 business 的方向、persistence 不反向依赖 business，以及 repository
不能结束绑定 session 的生命周期。结构检查带合法／违规样例，包含 import alias 与相对导入。
根 Ruff 配置只拥有通用规则。新增用例必须说明事务的原子集合、外部 I/O 所在作用域及 task 所有权；
静态 lint 不能证明并发 task 未共享 UoW，这仍需代码审查和具体风险的验收。

### 4. Resolver And Storage Form The Interpretation Boundary

- block hydration 负责隐藏 inline content / opaque storage pointer 分支。
- resolver 负责把 hydrated content 与所需 local relations 联合解释成 solved/use-facing value。
- storage 只负责由 pointer 取得 actual bytes；`WritableStorage` 还可以拥有 pointer serialization 与
  byte create/update/delete lifecycle，但不解释 MIME 或信息含义。
- resolver contract version 由 exact resolver ID 选择。extension 即使 disabled，已安装 decoder 仍需
  能读取其 persisted blocks；unknown version 明确失败。
- solved value 是 derived runtime projection，不是与 blocks/relations 并列的 durable authority。

### 5. Application Owns Retrieval Support；AI Execution Is Graph-Blind

- application/use capability 负责 graph projection、derived retrieval-record lifecycle、ranking 与结果合同。
- `AIManager` 只把 typed embedding/chat 请求路由到 AIModel → AIProvider → dialect adapter；它不理解
  Block、Relation、Resolver、organization 或 retrieval policy。
- `AgentManager` 把一个 persisted Agent definition（system prompt、model、Tool set、nullable tool choice、per-turn
  model-call budget）绑定为可复用的 Thread runtime。它依赖 graph-blind AIManager，但 Tool handler 的领域能力由
  调用方模块提供；Agent domain 本身不取得 organization、Resolver 或 graph authority。
- Agent Tool handler 是语义 owner 附近的 controller：它拥有 Agent-facing input model、参数接合和 JSON
  projection，并调用普通领域 service。领域 service 不接受 Agent message、prompt 或 Tool wire shape；Agent registry
  只拥有 exact ID 绑定、schema 暴露和执行机制。共享 projection helper 只能内聚表示机制，不能重新形成跨领域
  `agent_tools` 业务集合。
- Agent Tool input 由 Agent runtime 根据 handler 的 Pydantic model 只校验一次。一个 Turn 是消息历史的唯一
  writer；并发 ToolCalls 只返回结果，完整 Assistant ToolCall + ToolResult batch 才原子追加到 Thread history。
- Thread persistence backend 拥有完整 Thread snapshot。当前只有 process-local in-memory backend；不存在独立的
  Turn、ToolCall、ToolResult、Message 持久化关系，也不承诺 checkpoint、resume 或 Agent-level exactly-once。
- `SemanticRetrievalManager` 是 projection/profile/record/ranking owner。Block semantic input 只来自 Resolver
  `get_text()`；Relation semantic input 由 RelationService 组合 from-label、exact relation content 与 to-label，保留
  `to is from's property` 的方向语义。AIManager 只接收最终 typed text batch。
- semantic `maintain` 只扫描 missing/stale records，并越过 unavailable entity；`rebuild` 以调用开始时间为 cutoff。
  Manager method 不创建 job/dirty/lease/retry lifecycle，且 projection/provider work 不持有数据库 transaction；完整
  有效 batch 才短事务 upsert。Exact typed Job Handler 可调用同一 method 并把 bounded report 写入 Job state。
- `retrieve` 只比较 timestamp-fresh、dimension-compatible、non-zero records，返回一个全局排序的真实
  Block/Relation 列表；它不隐式维护 records，也不生成答案。
- Block/Relation 写入不隐式生成或删除 embedding records。Profile-scoped records 是 derived support，freshness 由
  owning use capability 根据 database-owned timestamps 判断。
- `LexicalRetrievalManager` 只为 Block 建立 `label + optional text` record。`context="lexical"` 是 non-recursive
  Block-local Resolver projection；parent 不复制完整 child text。literal/substring/`simple` term ranking 返回真实 Blocks
  与 bounded evidence，不生成 transient search entities。
- lexical maintain 允许 Resolver 在 exact capability 内 materialize missing faithful text child，但 record owner 不取得
  OCR/ASR、Storage 或 graph-write ownership。media description/summary 属于 Organization interpretation；Organization
  只改变 graph，后续 lexical maintain 才更新 derived records。
- semantic 与 lexical maintain/rebuild 都有 exact typed Job Handler。Manager method 本身仍不创建 Job；Cron 只通过
  generic Job template 产生 occurrence。

### 6. Organization Improves The Existing Info-Base Explicitly

- organization 是为后续 use 改善既有 info-base 的能力，不是 collection lifecycle 或信息状态。Block CRUD、source
  collection 和 extension protocol ingestion 都不会隐式触发 organization。
- organization 没有统一 manager、dispatcher、behavior table 或专用持久实体。rumination、supersession、refinement、
  evidence stance、synthesis、existing-referent anchoring、duplicate assertion 与 extraction 是八个独立的精确
  `BehaviorResolver`；行为 descriptor 是对应 Resolver 的惰性普通 Block，候选以 `information --candidate for-->
  descriptor` 表达。
- 每个行为拥有一个独立 automatic Job 和 `core.organization.<behavior>` deployment config。Job 只承担调度与运行管理；
  Resolver 读取候选、构造起始证据、调用所选 purpose-built Agent，并由 behavior-owned exact command 写普通 Block/Relation。
  初始 seed 不限制 Agent 后续通过 retrieval、Resolver 或 graph navigation 继续探索。
- Organization-owned Agent definitions 组合各领域提供的读取工具，而不拥有其合同。`retrieve` / `get_entities`
  由 info-base 拥有；`get_entity_neighborhood`、`find_path`、`get_connected_components` 由 Graph Navigation
  拥有；`resolver` 的 controller 位于 Resolver 领域，`ResolverManager` 发现与分派，exact Resolver instance
  执行。MCP Sink 只投影同一 owner contract，不成为 Organization 的依赖，也不继承内部 Agent Tool 的请求包装。
- Extraction 的专用取材工具消费已有 Resolver，并在本次运行内保留逐次观察；提交时校验原文片段，结果内嵌必要依据。
  来源 Relation 只提供当前内部入口，删除来源不删除结果及引文。该行为不要求 collection freeze、通用版本服务，
  也不改变其他 Organization 行为的读取或写入规则。具体机制见 [Organization](organization.md)。
- 工具定义表达关系含义，字段名称保留所指实体身份；行为识别过程属于所选 Agent definition。
  Resolver 的方法参数由实际 owner 逐调用验证，错误不丢弃同批其它结果。schema 由同一方法合同投影，
  顶层分支同时显示字段形状，以兼容只从顶层 properties 推断参数类型的 provider。
- 精确写入工具只接受最小 graph proposal，并校验 endpoint、cycle/opposite stance、source basis 或 occurrence-local path
  等机械不变量；开放世界的 referent、scope、authority、evidence、duplicate 与 synthesis 语义仍由相应 Agent 判断。
  所有结果追加到普通图，不创建 evaluated/no-op state、behavior report、relation-content registry 或级联引擎。
- `RuminationBehaviorResolver.ruminate(block_id)` 保持原有显式 focal 与 Peer 路径。它从 focal Resolver `get_text()` 与
  全部一跳 direct Relations 构造上下文，不递归探索，也不按关系数量截断；other endpoint 只投影 Block reference、
  resolver ID 与 `get_label()`。原有
  `core.organization.rumination.v1` Peer capability 与 draft/submit graph Tool IDs 保持兼容。
- draft-capable Resolver 显式拥有简短 description、Pydantic input model 与 `create_graph(input) -> StarsGraphForm`。
  Agent run 只在 Tool schema 中看到当前 exact Resolver IDs；具体 input schema 通过 `get_draft_graph_schema` 按需读取。
- Agent runtime 对 `draft_graph` 的通用 payload 与 selected Resolver input 完成同一轮 Pydantic validation；Tool handler
  只调用 Resolver create，再交给 InfoBaseManager normalization。在所附 rumination definition 中，
  `submit_graph(GraphForm)` 是唯一 graph-write Tool。
- rumination 的显式调用与 automatic Job 都是 additive、best-effort attempt。不能理解或模型诚实 no-op 不写图；
  automatic Job 本身不持久化 behavior report，也不自动建立 schedule。
- `interpret_missing_media()` 是独立 system-driven approach。它扫描尚无 `interpretation` relation 的
  image/audio/video Blocks，按 modality 选择 deployment-owned Agent，把 solved media 作为 canonical AI content part 交给
  Agent，并只接受现有 graph Tool 的 additive result。它不写 lexical records，也不是 Resolver faithful materialization。

### 7. Peer Discovery Routes Heterogeneous Runtime Capabilities

- Peers share database authority but may have different runtime abilities。`PeerManager` owns local inbound/outbound
  registries、full-snapshot publication、database-time liveness filtering and one-shot delegation；it does not understand
  capability payloads。
- Business owners retain typed codecs and non-delegating local seams。Current exact inbounds are
  `core.semantic_retrieval.v1`、`core.feature_retrieval.lexical.v1`、`core.organization.rumination.v1` and exact-target
  `core.extension.management.v2`。
- Extension management accepts exact-version `select` and `unselect`, `status`, enable, disable and config patch. A
  selection is Peer-local; shared Extension config/state remain deployment-wide. Python environment application pauses
  that Peer and is followed by a fresh process; unsupported distributions are rejected by Core instead of being silently
  routed through the Python installer. Older Peers reject the unknown action rather than routing elsewhere.
- `core.peer.protocol.http.v1` owns normalized query/headers/body envelopes、Peer JWT and HTTP response projection。
  Generic failover occurs only after pre-dispatch failure or exact `InkCre-Peer-Execution: not-executed`；a normal domain
  response or outcome-unknown stops。
- `route_to_peer` is caller-local routing policy。It never enters the capability payload/advertisement and an exact target
  is never substituted。There is no generic invoke route、generic delegation job or readiness advertisement。

### 8. Sink Projects Info-Base Use Into External Work

- `SinkManager` 拥有 exact Sink type registry、persisted instance config 与 Peer-scoped enable intent；`SinkBase` 拥有一个
  running instance 的 active resources。Registration 不创建或自动运行实例。
- Sink 是 application/use 的下游 projection，不取得 Block、Relation、Resolver、Storage 或 retrieval authority。
  一个 Extension 可以交付 Sink type，但 Extension enable 不等于 Sink instance enable。
- `cli/` 是独立分发的 REST consumer，也属于产品意义上的 sink，但不是 `SinkBase` runtime instance。它不安装
  Core、不直连数据库、不注册 Peer。普通 REST route 交给对应领域 owner；它不复用 Peer inbound 作为公共 API。
- `core.mcp.v1` 是首个实现：它把现有 retrieval、graph navigation 与 Resolver read behavior 投影为 MCP actions；
  oversized/binary content 通过 live Resource URI 重新读取当前 authority，不产生 Resource table 或缓存 authority。
- MCP 的 read-only boundary 排除 Agent-intended mutation command；Resolver `get_*` / `read_*` 仍可按其既有 contract
  lazy materialize missing derivation，因此相关 Tool 不虚假声明绝对无副作用。
- `core.agent-query.v1` 把一个 persisted Agent definition 绑定为异步 query instance；其动态
  `/sinks/{id}/query` 只创建 `core.sink.agent-query.v1` Job。Job handler 调用 Agent runtime，读取能力仍由各领域
  owner 提供，`submit_query_result` 由 Agent Query Sink 拥有。回答与支持它的 Block/Relation references 写入 Job
  state；Sink 不建立第二套 query、Thread 或 result store。

## Cross-Subtree Constraints

配置输入先合并成完整候选并验证，再持久化。普通记录读取不因 schema 未加载而拒绝；需要 SecretStr、嵌套
模型或 union 的执行路径接受一次原生 Pydantic 类型恢复。已经得到正确类型的内部值直接传递，不反复
dump/model_validate。不要把 model_construct 当作通用递归 decoder，也不要把内部或输出验证错误伪装成
HTTP 422。普通 REST 的请求输入、错误位置和内容表示见 [rest-interface.md](rest-interface.md)。

### Allowed Direction

当前允许的结构性方向可以理解为：

- extension 扩展 source / resolver
- source 或 extension-owned protocol adapter 把 canonical collection command 交给 info-base persistence
- info-base 通过 resolver / storage 解释内容
- application/use capability 从 info-base / resolver 路径消费内容并维护 derived retrieval view
- application 只把 typed AI input 交给 graph-blind AIManager
- Semantic retrieval 通过 DeploymentConfigManager 解析 deployment default Profile，但 config manager 不取得
  profile existence、maintenance 或 ranking 语义
- organization/application 可以把准备好的初始 Message 与 caller-owned Tools 交给 AgentManager；Agent Tool
  handler 再显式调用自己的领域 owner
- Resolver-native graph draft 先保持 StarsGraphForm authoring，再由 InfoBaseManager 分配 signed local IDs；ResolverManager
  不持久化 graph，AgentManager 也不取得 Resolver/InfoBase ownership
- business capability facade may call its own local implementation or encode a Peer protocol envelope；provider inbound
  always calls the explicit non-delegating local path，preventing delegation loops

### Important Asymmetry

- Resolver 的 `get_text()` 是通用 Block interpretation，不是 embedding-specific hook。
- AI capability declarations/model routing 不反向进入 Resolver；retrieval owner 负责两者之间的 projection。
- Shared AI Provider credentials/config 可以持久化在数据库；peer-local adapter availability 仍是 runtime fact。
- Agent definition 是 shared database fact；Thread state、bound Tool handlers 与正在执行的 Turn 是 peer-local
  runtime fact。Agent definition 更新不会改写已经创建的 Thread snapshot。

## Non-Goals

本文件不承接以下内容：

- source 调度实现细节
- 某个 extension 的 native API、relation grammar 或 canonical schema
- storage 的 built-in ID、表或 migration 约定
- application 某个 retrieve mode 的算法细节
- deployment / scheduler / runtime topology

这些要么属于 local `AGENTS.md`，要么属于 `docs/40-deployment/`。
Memos 的已验证实现合同由 [memos-extension.md](memos-extension.md) 负责。
RSS/Atom source vertical 由 [rss-extension.md](rss-extension.md) 负责。
Semantic retrieval、embedding records 与 rumination 的内部合同由
[semantic-retrieval.md](semantic-retrieval.md) 负责。
Lexical feature retrieval、media textualization/interpretation boundary 与 lexical records 由
[lexical-retrieval.md](lexical-retrieval.md) 负责。
