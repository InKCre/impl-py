# Capture Extension 与数据库 schema

`inkcre/capture` 拥有跨平台材料、图、收入和恢复合同，见 [Capture Extension 合同](https://github.com/InKCre/docs/blob/main/20-product-tdd/capture-contract.md)。原生分发在本 Peer 内完成收入；Python 分发提供自身支持的读取能力，不成为其他分发的写入执行者。

Capture 不增加专用共享表、HTTP API 或 SQL RPC。Mac 将草稿、待办和确认结果保存在 SQLite；收入使用标准 Block／Relation／Storage，并通过根 metadata 的提交身份和材料摘要恢复。Python 分发只读取同一图合同，不参与 Mac 的收入事务。

数据库的 Extension participation 分发标识是开放字符串；宿主只在加载边界验证自己支持的格式。未发布候选 dcf44bc9af73 仅移除原 python/module_federation 白名单，不再创建 Capture 表。

Python 分发保留 Capture／Attachment resolver，图片通过标准 storage 读取；不公告采集执行服务。共享图结构未改变，不需要 Python Peer 或 PostgREST 在线才能从 Mac 收入。

迁移回退会恢复旧分发约束；信息库已经保存其他原生分发时，数据库会拒绝恢复该约束。回退应保留参与记录并处理部署版本，不得通过删除记录或把分发标识改写为 Python 来强行完成。
