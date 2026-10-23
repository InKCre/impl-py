# Capture Extension 与数据库 schema

`inkcre/capture` 拥有跨平台材料、图、收入和恢复合同，见 [Capture Extension 合同](https://github.com/InKCre/docs/blob/main/20-product-tdd/capture-contract.md)。原生分发在本 Peer 内完成收入；Python 分发提供自身支持的读取能力，不成为其他分发的写入执行者。

本仓库交付权威数据库 schema／迁移。未发布候选 dcf44bc9af73 创建 capture_receipts，材料和图摘要均为必填，并允许 swift_bundled 参与格式。根 ID 不级联删除，回执可证明历史收入，避免延迟重试复活内容。没有 Capture 专用 HTTP API、SQL RPC 或 Core 业务 UoW，事务由原生 Capture 分发编排。

Python 分发保留 Capture／Attachment resolver，图片通过标准 storage 读取；不公告采集执行服务。共享图结构未改变，不需要 Python Peer 或 PostgREST 在线才能从 Mac 收入。
