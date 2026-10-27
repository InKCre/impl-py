# resolver/ Local Guide

本文件只描述 `app/business/info_base/resolver/` 的局部事实与高风险边界。上层 contract 看
[info_base guide](../AGENTS.md) 与
[business-pipeline-and-authority.md](../../../../docs/30-unit-tdd/business-pipeline-and-authority.md)。

## 何时阅读

- 修改 `ResolverManager` / `Resolver`；
- 新增、删除或 version 一个 resolver；
- 修改 hydration、relations、solved snapshot 或 capability effect；
- 修改 resolver identity/dedup。

## 关键文件

- `main.py`：exact registry、resolver instance、relation/solved caches；
- `contracts.py`：十个 core IDs 与 typed failures；
- `bootstrap.py`：core decoder registration；
- `inspection.py`：bounded byte inspection helpers；
- `label.py`：shared whitespace normalization and 96-code-point identifier bound；
- `{text,html,markdown,image,audio,video,pdf,epub,zip,file}.py`：exact core contract versions。

## Construction And Registration

- 不 override `Resolver.__init__()`；subclass 使用 `__post_init__()` 和 `_get_solved_content()`。
- Resolver 通过 info-base commands/services 和绑定 UoW 的 repositories 使用持久化能力，
  不创建 session；外部内容获取不能占用图写事务。
- `Resolver.__init_subclass__()` exact-register decoder；同 class 重复 idempotent，不同 class 抢同 ID 失败。
- Extension runtime disable 不撤销已 import 的 Resolver class registration。Registration 在进程内单调保留，
  使 persisted Block decoder 在 active Extension route/resource 关闭后仍可用；exact version replacement 仍以进程
  restart 为边界。
- `register_core_resolvers()` 独立于 extension sync/start；extension disabled 不能让 core decoder 消失。
- Resolver ID 必须 namespaced + versioned。共享 semantic IDs 固定为十个 `core.<kind>.v1`；不保留 bare alias。
- Agent graph drafting 是 opt-in capability：subclass 同时声明 `draft_description`、`draft_input_model` 并实现
  `create_graph(input) -> StarsGraphForm`。普通 decode-capable Resolver 不会因此自动成为 draft-capable。

## Hydrated And Solved Content

- Resolver 的 content read 委托 `BlockModel.get_hydrated_content(refresh=...)`；不要自行解析 storage pointer。
- Inline content 是 `str`；configured storage 必须 hydrate 为 `bytes`。
- Solved content 是 resolver-instance snapshot，不是 durable authority。`refresh` bypass 并替换 hydration、relation
  和 solved caches 中被请求的 snapshot。
- 现存 `get_raw_content()` 只是 resolver→block hydration 的低层兼容入口；新 contract/prose 使用
  `hydrated content`。

## Capability Outcomes

Concrete resolver 必须实现：

- `get_text(refresh=False, materialize_missing=True) -> str | None`
- `get_label(refresh=False) -> str`

`UnsupportedResolverCapability` 表示 exact contract 没有该能力；`None` 表示能力存在但当前 block 没有有意义的
result；empty string 只能来自真实 authored/derived empty。`UnknownResolverError` 是另一类 registry failure。

十个 core semantic resolvers 中，text/HTML/Markdown 提供 text；image/audio/video/PDF/EPUB/ZIP/file 在没有真实
OCR/STT/extraction 能力前显式 unsupported，不用 metadata 伪装正文。Embedding/retrieval owner 只消费这个
通用 projection，不要求 resolver 理解 AI model/profile。

`get_label()` 是 required、concise、Block-local 的 resolver-qualified reference。它不得遍历 Relation、调用 AI 或
materialize graph；identifier 缺失时返回 resolver 自己的 readable kind。Exact resolver ID 不能进入 label。
RelationService 用两端 label + exact relation content 投影 directed dynamic property，因此 retained label format 的
不兼容变化必须推进 resolver contract version。

`draft_input_model` 是 Resolver-owned authoring contract，不是 persisted `block.content` schema。Agent runtime 对 selected
Resolver input 做 Pydantic validation；Resolver method 接收 ordinary typed input，不接收 `validated_*` wrapper。

## Effect And Relation Vocabulary

- `refresh`：替换 local snapshot；
- `materialize_missing`：允许创建 absent derivation；
- `recompute`：未来 organization command 的 existing derivation regeneration；
- `invalidate`：只丢 cache。

不要增加 `force` / `reload` 同义参数。Direct relation query 的 `include_in/include_out` 相对 subject block；
不是 traversal mode。

## Media Matching

`ResolverManager.match_media_type()` 只把一个具体 MIME 匹配到已经注册的 exact core ID，并拒绝 generic
octet-stream。Protocol/source extension 自己拥有 declared MIME、HTTP MIME、filename、byte signature 的顺序和
`core.file.v1` fallback。

## Identity Risk

默认 `get_existing_async(blocks)` 仍按 `resolver + content` 查重。改变它会改变 block identity，不是 resolver-local cleanup；
必须回到 owning source/unit 的 exact identity contract，禁止用 fuzzy content/time match 偷换。

## 异步数据库边界

`get_transfer_url()` 需要读取 storage catalog，因此现在必须 await。具体 Storage 的 URL 格式化仍是
普通函数。`get_existing_async(blocks)` 通过绑定事务的 BlockRepository 查询精确身份，不接受 raw
session，也不自行提交；所有 Stars 消费者调用此接口，不保留同步身份查询。
