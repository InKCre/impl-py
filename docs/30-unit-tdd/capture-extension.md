# Capture 数据库协议与可选读取分发

跨 Peer 的图、材料指纹和提交语义归 [主动采集持久化契约](https://github.com/InKCre/docs/blob/main/20-product-tdd/capture-contract.md)。Mac 自行构图并通过 PostgREST 写入；本仓库拥有 schema、迁移和 `commit_capture` 的数据库事务实现，不作为提交服务。

`dcf44bc9af73` 引入 `capture_receipts`；后续迁移增加数据库计算的 `graph_digest` 与 `commit_capture`。函数以 SECURITY INVOKER 运行，复用既有认证角色，固定 search_path；图、标准 binary storage 和回执处于同一个事务。协议投影由 `app/database_contract/protocol.py` 维护，表模型由 `app/schemas/capture.py` 注册。旧回执和本机待办的材料摘要保持兼容。

`inkcre/capture` Python 分发仅提供 Capture／Attachment resolver，在使用 Python 读取共享图时按需安装。它不公告提交能力、不注册提交 API、不承担 Mac 写入；Mac 收入无需启动或安装此分发。Python 与 Web 读取分发分别要求其声明的 Host SDK，两者互不依赖。

数据库测试只运行于显式指定的可销毁实例，验证认证角色调用、并发重放、摘要与图冲突、根删除重放及写入失败回滚。Mac 的 SQLite 队列和网络丢响应恢复由原生仓库验证；编译或本地模拟不能冒充无 Python Peer 的实际写入证据。
