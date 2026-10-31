# source/ Local Guide

本文件只描述 `app/business/source/` 的局部事实与变更风险。跨 subtree contract 看
[business-pipeline-and-authority.md](../../../docs/30-unit-tdd/business-pipeline-and-authority.md)。

## 何时阅读

- 修改 `SourceBase`、`SourceManager` 或 Source-owned Job handlers；
- 修改 source registration/config/state；
- 修改 Source collect/backfill command validation 或 execution；
- 新增 incremental source。

## 三层对象

- `source type`：registered class/capability identity；
- `source instance`：持久 config、long-lived state、optional Storage 与 lazy Block anchor；
- `Job`：global one-shot command envelope；Source handlers 只拥有 collect/backfill parameters 与 execution。

不要把配置、cursor 或 execution progress 混到同一层。

## Registration

- `SourceBase.__init_subclass__()` 只登记内存 class；import 不连接数据库。
- Source class 的 `source_type` 是稳定持久身份，移动/重命名实现时保持其值。第一方类型显式声明既有
  path 字符串；旧第三方 wheel 未声明时仍以该 class 自己的 module/qualname 注册，不继承父类身份。
- 同一 class 重复注册幂等，不同 class 不得抢占同一 type ID；身份变更是数据合同变化，不是普通重命名。
- `await SourceManager.sync_source_types_async()` 在显式 bootstrap reconcile catalog。
- Extension source 必须在 extension startup import；否则 source type 不存在于当前 runtime registry。
- Extension runtime disable 不撤销已 import 的 Source class registration。Registration 在进程内单调保留；
  source instance 只有被 command 调用时才运行，active Extension route/resource 则由 Extension lifecycle 单独撤销。

## Config And State

- `SourceModel.config` 是 validated source-instance config；`SourceModel.state` 是 long-lived conditional/cursor
  state；`JobModel.parameters/state` 只属于一次 execution。
- Cursor、ETag、Last-Modified、watermark 必须同时声明 authority scope；config/native identity 改变时不可盲目复用。
- `SourceBase.get_config/get_state/set_state` 以 async 方法每次从 database 读取／写入，不缓存另一份 authority。

## Job-Only Execution Path

- Manual collection 创建 exact `core.source.collect.v1` 或 `core.source.backfill.v1` `PENDING` Job。
- Global Cron 只创建 typed Job；Source row 不拥有 schedule，也不知道调用是否来自 Cron。
- `JobManager` 在 claim 前通过 registered handler 验证 parameters 与 `can_handle()`，再以 conditional update 原子
  claim `PENDING -> RUNNING`；无法 claim 返回 `False`。
- Source Job handler 才解析 Source/type-specific command config 并调用 `collect()` / `backfill()`。
- 成功/失败只从仍为 RUNNING 的 row 关闭；DB trigger 拥有 lifecycle timestamps；generic manager 不 retry Job。

因此不存在 scheduler 直接执行 source 的第二条路径。修改 schedule 时必须保持“schedule creates command”与
manual path 一致。

## Collection And Persistence

- Source 负责 native fetch/adapter/policy，可以产生 graph form 或调用 owning repository/application service。
- 新采集用例组合 SourceUnitOfWork 的 graph、sources、configuration；source 不复制通用 persistence。
- ensure_block_async／resolve_writable_storage_async 接受必需的 SourceUnitOfWork；anchor、graph 与 cursor 按用例共同提交，不接受 raw session。
- `collect()` 不吞异常或假装成功。Unit 自己定义 per-item transaction、accepted partial effects 与 state advance。
- Collection 不调用 organization hook。Organization 是独立 lifecycle，不得重新塞回 Source command。

## Incremental Identity

- Exact native identity优先；不足时显式选择 create/discard/duplicate reduction，不用 fuzzy overwrite。
- Time watermark 可做 admission heuristic，但不是 identity/reconciliation correctness。
- Long-lived state 只在 owning unit 的 success boundary 推进；`304`、fatal parse、primary failure 是否推进必须有
  explicit source contract。
