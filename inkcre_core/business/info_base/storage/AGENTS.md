# storage/ Local Guide

本文件只描述 `inkcre_core/business/info_base/storage/` 的局部事实与风险边界。上层 contract 看
[info_base guide](../AGENTS.md)。

## 何时阅读

- 修改 `StorageManager`、`Storage`、`WritableStorage`；
- 新增/删除 storage type 或 built-in storage；
- 修改 opaque pointer、byte lifecycle、catalog/bootstrap；
- 修改 block hydration 与 storage 的边界。

## 关键文件

- `main.py`：registry、catalog sync、base/writable interfaces；
- `http.py`：bounded HTTP(S) byte retrieval；
- `postgresql.py`：deployment-owned PostgreSQL byte CRUD；
- `app/database_contract/profile.py`：built-in catalog authority。

## Stable Contract

- Storage 输入是自己拥有 grammar 的 opaque pointer string，输出是 actual bytes。
- Storage 不拥有 MIME、filename、information kind、resolver ID、embedding 或 block identity。
- `await WritableStorage.create_content(bytes, storage_repository)` 是 common create seam，返回可直接持久化为
  `block.content` 的 pointer string；storage handler 自己 serialize internal key。
- Low-level async read/write/update/delete 接受已绑定事务的 StorageRepository，不得自行 commit；用于 application command 与
  graph mutation 协调。
- 原地 update pointer-addressed bytes 不更新 block row/cache/embedding；这是 storage 与 block 独立 authority 的
 代价，不通过 storage→block 反向依赖隐藏。

现存 method 中的 `raw_content` 是 mechanics API name，表示未被 resolver 解释的 bytes，不是第二套 block
representation。Shared/domain prose 使用 `actual bytes` 与 `hydrated content`。

## Registry And Bootstrap

- `Storage.__init_subclass__()` 只注册内存 class，不连接数据库。
- `await StorageManager.setup_builtin_storages_async()` 在显式 runtime bootstrap 中 reconcile catalog。
- Built-in handler type 是短 ID，因此 `storage/__init__.py` 必须 import 当前 handlers；dynamic dotted-path fallback
  只服务真正使用 dotted class path 的 custom type。

## Current Built-Ins

| Storage ID | Type | Capability |
| --- | --- | --- |
| `-1` | `http` | read-only bounded HTTP(S) bytes |
| `-4` | `postgresql_binary` | deployment-owned byte C/R/U/D |

Retired `-2/-3` catalog rows可能存在于历史数据库，但当前 code/profile 不注册或产生 semantic HTTP storage。
不要重新增加 `http_image`、`http_video`、`http_html`；semantic interpretation 属于 exact resolver。

## PostgreSQL Binary Storage

- `storage_blobs` backing relation 只拥有 UUID + bytes；它不是 storage type 或 information object。
- Pointer 当前是 storage-owned JSON `{ "blob_id": "<uuid>" }`；application/extension 不得 hard-code。
- Native core commands使用 GraphUnitOfWork.storage；PostgREST peer 使用 admitted raw create/read RPC 与 exact row update/delete。
- Referenced `storages` catalog row deletion是 `RESTRICT`；删除 storage blob 不反查或改写 blocks。

## HTTP Storage

- 只接受 HTTP(S) pointer；timeout、redirect 与 maximum response bytes来自 storage config。
- 同时检查 declared length 与 chunked received size；返回 bytes，不按 Content-Type 选择 resolver。

## 编辑指引

- 新 storage 优先回答 pointer grammar、byte limit、read/write capability 和 deletion ownership；不要从 media kind
  派生 storage family。
- S3/Nextcloud 等 future storage 复用同一 byte contract；source/application 只依赖 common create seam。

## 异步数据库边界

新路径通过 `StorageManager.get_storage_async()` 读取 catalog，读取 scope 结束后才执行外部下载。
PostgreSQL bytes 使用 `read_content/create_content/update_content/delete_content` 和必填的
StorageRepository；GraphUnitOfWork 使 bytes 与图处于同一事务。不得重新加入接受 raw session 的兼容接口。
