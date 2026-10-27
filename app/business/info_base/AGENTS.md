# info_base/ Local Guide

本文件只描述 `app/business/info_base/` 的局部事实、术语和编辑边界。跨
`extension/source/info_base/application` 的结构先读
[business-pipeline-and-authority.md](../../../docs/30-unit-tdd/business-pipeline-and-authority.md)。

## 何时阅读

- 修改 `InfoBaseManager`、`BlockService`、`RelationService`；
- 修改 block hydration、resolver/storage contract；
- 修改 graph persistence、identity/dedup 或 embedding invalidation；
- 新增 common semantic content resolver。

## 关键文件

- `main.py`：`InfoBaseManager`，StarsGraphForm normalization 与 GraphForm persistence；
- `block.py`：block CRUD/fetchsert；
- `relation.py`：relation CRUD/fetchsert、direct direction query 与 endpoint-label dynamic-property projection；
- `resolver/`：exact decoder registry 与 use projection；
- `storage/`：opaque pointer → actual bytes；
- `app/schemas/info_base/block.py`：persisted block 与 instance-local hydration cache。

## Content Vocabulary

- `persisted content`：`BlockModel.content` 字段；inline block 上是 actual text，storage-backed block 上是
  opaque pointer string。
- `hydrated content`：`BlockModel.get_hydrated_content()` 返回的 inline `str` 或 storage-backed `bytes`。
- `solved content`：resolver 根据 hydrated content 与 direct relations 形成的 typed/use-facing projection。
- `semantic content block`：用 exact core resolver ID 表达 text/image/audio/video/PDF/EPUB/ZIP/file 等信息的
  ordinary block。

不要在新文档/API 中重新引入 “real content” 或把 “raw content” 当作第二套 domain representation。Storage
与 resolver 内仍存在的 `get_raw_content` 是低层兼容 method name；它不改变上述共享 contract。

## Persistence And Graph Facts

- Source/extension 可以提出 `StarsGraphForm` 或使用必需的 GraphUnitOfWork 组合 graph command；
  info-base commands 拥有图写入用例，persistence repositories 执行 SQL。
- Draft-capable Resolver 仍只产生 rooted `StarsGraphForm`；Agent-facing `draft_graph` 是 Resolver create +
  `InfoBaseManager.normalize_graph` 的 thin wrapper，`submit_graph` 才进入 persistence。
- `persist_graph` 批量先落 Blocks 再落 Relations；relation identity 当前是 `from_ + to_ + content`。
- Caller 传入 session 时，manager/helper 不得擅自 commit。
- Helper transaction boundary 不自动成为产品级 complete-graph guarantee；owning command 声明 partial effects。
- 默认 `Resolver.get_existing()` 仍按 `resolver + content` exact match；source-specific identity ladder 可在其
  repository/resolver 中拥有更强规则，不能在 route 层 fuzzy overwrite。

## Block Hydration

- `block.storage is None`：hydrated content 是 `block.content`。
- `block.storage is not None`：`block.content` 只交给 selected storage handler；handler 必须返回 bytes。
- Hydration cache 存在 Pydantic private state，以 `(storage, content)` 为 key，不映射到数据库、不进入 transport。
- `refresh=True` bypass 并替换当前 block instance snapshot；不承诺其他 ORM/peer instance invalidation。
- Storage-backed bytes 可独立变化，所以 `block.updated_at` 只表示 row 更新时间，不是 actual content freshness。

## Resolver Boundary

- `ResolverManager` 只按 exact ID select/register；unknown ID 明确失败，不存在 default decoder fallback。
- Core bootstrap 独立于 extension loading，注册十个 `core.<kind>.v1` semantic resolver。
- Duplicate registration：同 class 重复注册 idempotent，不同 class 抢同 ID 抛错。
- `get_text()` 的 unsupported、supported-null 与 authored-empty 必须保持可区分；不要添加 use-specific
  `get_*_for_embedding()` projection。
- `get_label()` 必须 concise、Block-local、resolver-qualified；RelationService 使用
  `subject/from label + exact content/property + value/to label`，不增加 RelationResolver。
- Resolver 可显式使用 `materialize_missing` 触发 absent derivation；读时写 graph 不是天然错误，但必须由 exact
  capability contract 声明。
- `include_in/include_out` 相对 subject block，且只筛 direct relations。
- `ResolverManager.get_draft_capabilities()` 只枚举显式声明 draft input model + description 的 Resolver；不要把所有
  persisted/source-native content schema 自动视为 Agent 可创作 schema。

## Storage Boundary

- Storage 只解释自己的 pointer grammar 与 byte mechanics；不决定 MIME、filename、block kind 或 resolver。
- Writable storage 的 common create seam 接收 bytes 并返回可直接写入 `block.content` 的 pointer string。
- Extension 拥有 source/protocol-specific classification evidence order；`ResolverManager.match_media_type()` 只做
  exact common match。

## Embedding Ownership

- Block create/update 不生成、删除或更新 embedding record；profile-scoped records 是 use-owned derived support。
- Freshness 由 Profile、Block/Relation 及 endpoint timestamps 在消费时判断；storage bytes 原地变化仍不更新
  Block row，因此也不会自动改变该 freshness watermark。

## 编辑指引

- 修改 exact resolver ID、hydration/effect vocabulary 或 metadata→semantic pattern 时，先核对 Hub
  `knowledge-capability-contract.md`。
- 单一 protocol 的 relation grammar、identity ladder 与 transaction 留在 owning extension Unit TDD。
- 不用新增 generic metadata JSON、resource binding 或 media storage family 来绕开现有 graph/resolver boundary。

## 异步数据库边界

- 新 Graph 应用入口位于 `commands.py`，事务内组合使用必填 `GraphUnitOfWork`，不能传 raw session。
- `app/persistence/info_base/repository.py` 只接收 session 并执行查询／写入／flush，不允许创建 session 或 commit/rollback。
- `app/persistence/info_base/uow.py` 拥有工厂组合，使用 SQLAlchemy 原生 framing，不跨 task 共享。
- 独立入口使用 BlockService／RelationService；组合写入调用 persist_graph／persist_stars。不保留同步或可选 session 接口。
