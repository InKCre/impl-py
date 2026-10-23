# Organization

Shared Product language and cross-unit contracts remain owned by `docs/_shared/`. This document records only the implemented
core-py topology and exact local contracts.

## End-to-end topology

```text
automatic Job / explicit rumination or extraction
  -> exact BehaviorResolver
     -> behavior-owned initial context
     -> purpose-built Agent selected by core.organization.<behavior>
        -> definition-selected retrieval / Resolver / graph navigation
        -> no graph effect
        -> exact behavior mutation tool
           -> ordinary Block / Relation transaction
              -> later Resolver, retrieval, navigation, or application use
```

Organization improves an existing info-base for plausible later use. It neither predicts an exact future query nor reorganizes
for structural neatness. Automatic Jobs bound the number of initial seeds, not the size of every seed's context or subsequent
Agent exploration. Rumination's focal context includes all direct relations; exploratory behaviors use a relation-count limit
for their initial seed context.

## Behavior carriers

Core implements eight independent Resolver classes: rumination, scoped supersession, non-dominating refinement, evidence
stance, provenance-preserving synthesis, existing-referent anchoring, provenance-aware duplicate assertion, and extraction. There is no
generic Organization manager/base/dispatcher or persistent behavior table.

Each behavior Resolver provides:

- a stable, versioned Resolver ID and readable description;
- a lazy descriptor Block using that Resolver;
- `record_candidate()` for an exact `candidate for` edge;
- its own automatic availability and execution method;
- its own exact graph command where the behavior has a precise mutation.

Resolver registration remains the single extensibility mechanism. The candidate tool discovers registered classes by their
small structural capability; Core does not maintain a second behavior registry. An Extension can therefore provide another
exact behavior Resolver and its own execution/config/Job contracts without modifying a central Organization map.

## Behavior relations and commands

Relation content is owned next to its writer and reader, not in a global registry:

- `successor --supersedes--> predecessor` rejects a currently visible directed cycle;
- `detail --refines--> predecessor` rejects a currently visible directed cycle;
- `evidence --supports/challenges--> assertion` rejects the opposite stance for the exact pair;
- every material source `--synthesis--> derived text Block`; changed reapplication also writes
  `previous synthesis --edited--> new synthesis`;
- `source --has mention--> occurrence-local selected-text Block --refers to--> existing referent`;
- lower Block ID `--duplicates assertion-->` higher Block ID.

These commands validate graph mechanics, not open-world meaning. They do not delete, merge, overwrite, assign confidence, infer
transitive closure, or reserve generic Relation writes. Sequential exact replay converges through fetchsert. Synthesis replay is
keyed by exact text plus exact source basis; the ordinary Block identity rule is not changed.

## Reading and Agent boundary

Exploratory behavior definitions select the following owner-provided read tools:

- info-base owns `retrieve` and `get_entities`;
- the Resolver domain owns `resolver` discovery and invocation;
- Graph Navigation owns `get_entity_neighborhood`, `find_path`, and `get_connected_components`.

Organization owns its behavior definitions, candidate selection, initial message, and behavior-specific tools.
The generic read contracts and Agent-facing controllers remain with their semantic owners; Organization does not redefine
them merely because its Agents consume them. MCP Sink and Agent Query Sink may compose the same owner contracts without
becoming dependencies of Organization.

提取专用的 `read_extraction_material` 在调用现有 Resolver 后保留本次读取的文字与标签，并返回运行内的
observation ID。它不重定义通用读取合同，也不要求采集器固定原材料。初始材料经过同一入口，可直接用于提案；
其他读取工具返回的内容不能替代提取观察。观察仅存于本次运行的 ContextVar 容器，逐次新增，退出或取消时清空。

Mutation tools are behavior-specific, except the single dynamic `record_organization_candidate` tool. Agent definitions—not an
extra runtime allowlist—select the tools appropriate to each behavior. AgentManager and AIManager remain graph-blind execution
infrastructure; they do not own Organization semantics or writes.

The supplied rumination definition retains only `get_draft_graph_schema`, `draft_graph`, and `submit_graph`. Its task is to
reconsider the supplied focal Block, not to search the graph or mark candidates for other behaviors. Other behaviors may still
mark a rumination candidate. These choices belong to Agent definitions, not an additional runtime enforcement layer.

## Automatic Jobs and configuration

Eight exact automatic Job handlers independently execute the eight behaviors with bounded `max_seeds`. They share Job lifecycle only;
there is no Evolution umbrella Job. Availability requires the corresponding deployment config and a locally executable Agent.
No schedule is created automatically.

The config keys are `core.organization.rumination`, `.supersession`, `.refinement`, `.evidence_stance`, `.synthesis`,
`.existing_referent_anchoring`, `.duplicate_assertion`, and `.extraction`. Each value contains only its selected Agent ID; model, prompts, tools,
tool choice, and turn budget stay in the Agent definition.

Candidate selection combines behavior-owned strong signals, recent Blocks, explicit `candidate for` edges, and a small random
fallback. A completed Job may write nothing. Missing/unavailable runtime prevents claim; unhandled provider, database, or model
execution failure uses the existing failed/timed-out Job lifecycle.

Automatic execution logs a selected Block disappearing or one Agent Turn reaching its model-call limit as a recoverable seed
failure, then continues with the remaining seeds. Already committed graph effects remain. If all attempts complete without a
batch-level failure, the Job finishes even when individual seeds failed; this is not a semantic success verdict. Configuration,
provider, database, unexpected execution errors, and cancellation still escape. Explicit focal rumination continues to report
budget exhaustion to its caller. These diagnostics use the existing application logger and configured backend, not a new report.

提取的配置 schema 为 `core.organization.extraction.config.v1`。自动 Job 为
`core.organization.extraction.automatic.v1`，显式 Job 为 `core.organization.extraction.explicit.v1`，后者接收
`{"block_id": 123}`，调用 `ExtractionBehaviorResolver.extract_local()`。两者复用既有 Agent、Job 和配置合同，
没有新增 HTTP 或 Peer 协议。自动执行遇到不能提供有意义文字的候选时记录并跳过，显式执行则报告该错误。

## 留存依据的内容提取

`core.organization.behavior.extraction.v1` 判断材料中是否存在值得独立引用、关联或复用的信息；自足消息、
短需求及已有结果可以零产出。参考 Agent 定义位于 `tests/organization/acceptance/agent_definitions.json`。
它不是默认启用的部署配置；部署方选择能执行工具的模型，建立 Agent 后再写入上述配置并提交 Job。

`create_extraction` 接收正文及每个贡献来源的一次观察和原文片段。它校验观察属于当前运行、每个来源只选一次、
片段在实际观察中精确且唯一出现且互不重叠，再按来源 ID 与原文顺序生成局部依据 key。模型不能提供历史出处或
组 key。历史出处只保存实际取得的 Resolver 标签，发言者归属由正文和必要引文表达。
机械校验不能证明正文忠实，因此仍需模型语义验收，也不为其他 Peer 写入相同类型提供真实性认证。

结果使用 `core.organization.extraction-result.v1`，在 content 内保存正文、历史出处和必要引文。普通来源 Relation
只表达当前内部入口，按共享合同以 `kind` 和 `basis_key` 对应依据组。来源变化、删除或解除关系都不改写结果；
关系缺失表示入口不可用，不能据此断言来源已删除。删除结果同时删除其内嵌依据，不删除来源。
结果 Resolver 由 info-base bootstrap 独立注册，不要求配置或运行提取 Agent。default/lexical 文本投影均区分正文与引文；
`get_source(basis_key)` 的映射错误不会妨碍读取合法正文。

每个产物在一个短 GraphUnitOfWork 中完成。提交先按 ID 顺序锁定仍存在的来源，再检查完整内容和全部来源映射，
只有精确一致才复用结果；否则同时写入结果及来源关系。READ COMMITTED 下，同一组来源的并发重放在锁后重查，
不会各自创建一份。模型等待和 Resolver 读取不占有这些锁。此保证只约束本行为的写入，不修复手工改动的图，
不增加语义去重、已处理标记或来源版本服务。
写入工具只返回结果 Block ID 和是否新建；来源关系通过既有图读取，不额外返回逐条关系回执。

本功能借鉴了 [Hister](https://github.com/asciimoo/hister) 对采集内容提取与后续复用的关注。
感谢 asciimoo 与贡献者的工作；当前实现没有复制其源码或资源，也不声称兼容 Hister。后续具体复用应独立核对许可。

## Graph use

`get_connected_components` 的通用查询合同归 Graph Navigation。Duplicate Assertion 只拥有它的消费规则：
一个完整的 `duplicates assertion` component 计作一个 provenance occurrence；truncated 结果不能证明临时分组
彼此独立。这里不重复定义遍历算法或返回模型。

`SupersessionBehaviorResolver.read_lineage()` follows `supersedes` relations in both directions from a focal Block and returns
the bounded graph, current frontier, cycle detection, and truncation. A relation points from successor to predecessor:
for C supersedes B and B supersedes A, the complete acyclic result has frontier C and retains A/B in its history. The frontier
contains Blocks with no incoming supersedes relation; a cyclic or truncated result has no current frontier. This read does
not validate semantic supersession or select a latest Block by timestamp.

这些读取直接 await GraphUnitOfWork 中的 repositories，不再通过 worker thread 执行同步 SQL。
公开组织行为命令各自拥有一个事务；合成的 Block、来源关系与 edited 关系，以及锚定的片段和关系，
都在同一事务中提交或回滚。内部协作只接收必需的 GraphUnitOfWork，不以可选 session 改变提交语义。
自动行为先在短作用域内读取 seed 与邻居，退出后才调用 Resolver 或 Agent；媒体解释的邻居按 ID 批量读取。
Other relations remain usable through ordinary navigation; no shadow Organization index is maintained.

## Best-effort limits

Organization can abstain, miss relevant information, or be wrong; semantic quality is evaluated through credentialed,
Human-reviewed information worlds rather than a claimed completeness score. External Storage pointer bytes can change without an
observable Block/Relation change, so synthesis reconsideration and all relation-based propagation remain best effort. The system
does not persist no-op/evaluated state, chain-of-thought, behavior reports, or a universal relation-force engine.

Media interpretation remains a separate existing Organization path and keeps its bounded report contract. Explicit focal
rumination keeps the existing Peer capability and HTTP request contract while its local implementation is now carried by
`RuminationBehaviorResolver`.
