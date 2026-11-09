# B 站资料读取

当 Python Peer 需要读取或索引 Web Peer 已采集的 B 站资料时，启用 `inkcre/bilibili` 的 Python Distribution。它与 Web Distribution 解释同一份共享图，不拥有采集任务或浏览器会话。

## 读取与索引边界

`extensions/bilibili/schema.py` 验证四类 v1 原生记录，拒绝未知字段、显式 null、非字符串身份和错误类型。User、Folder、Video 的原生身份保存在 Block 内容中；Membership 由所属 Folder 和指向 Video 的关系定位，不复制关联 ID。Python 解释这些已保存的资料。

Resolver 只从已持久化内容产生 Block-local 标签与文本。视频与收藏夹投影包括已保存的标题、简介，未知标题回退到 aid、BVID 或列表 ID。词法维护使用既有 `LexicalRetrievalManager` 和普通维护 Job；插件不直接写派生检索表，也不递归抓取相关视频以补全文本。

直接关系可通过 Core 的 Resolver 图接口读取。收藏条目的可用性与收藏时间独立于视频，避免不同收藏夹的观察互相覆盖。失效占位文字不得在采集阶段抹去旧标题；读取阶段如实解释已保存的较完整记录，不尝试恢复源站内容。

## Runtime 与交付

标准 Python Extension entry point 只装配 Resolver，没有 Source、HTTP 路由、Peer inbound 或私有后台任务。安装与启停沿用原有 Host；Python 类注册随进程保留，停用不模拟卸载已 import 的模块，本插件没有额外的 active effect。Host SDK 范围以 `app.version.CORE_VERSION` 的 `core-py` 边界为准，不把 `inkcre-core` 项目版本当成 Host 版本。

内容契约和用户使用范围见 [插件说明](../../extensions/bilibili/README.md)。发布、安装和精确版本选择继续遵守 [First-party Extension Distribution](../40-deployment/first-party-extension-distribution.md)。
