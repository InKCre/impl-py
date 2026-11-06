# B 站收藏夹读取

`inkcre/bilibili` 的 Python Distribution 解释共享信息库中的 B 站 v1 资料，为 Python Peer 提供读取与现有词法检索所需的文本投影。采集由同一插件的 Web Distribution 借用用户选择的浏览器完成；Python 读取不需要该浏览器在线，也不接触 B 站账号或 cookie。

## 使用

在需要解释或索引 B 站资料的 Python Peer 上安装并启用本 Distribution。Web 采集得到的账号、收藏夹、视频和收藏条目随后可由普通 Resolver 读取。现有 `core.feature_retrieval.lexical.maintain.v1` Job 负责维护索引；本插件只提供标签和文本解释，不另外写一套索引。

视频文本包含已保存的标题与简介，收藏夹文本包含名称与简介。未知标题使用原生身份作为可读回退；缺省字段表示未知。记录的直接关系继续通过普通图接口读取，包含收藏夹归属、成员与 UP 主。

## 持久契约

本版解释四个精确 Resolver ID：

- `extensions.bilibili.user.v1`：B 站账号，含 MID 和实际取得的名称。
- `extensions.bilibili.folder.v1`：收藏夹，含完整列表 ID、名称、可见性及来源报告数量。
- `extensions.bilibili.video.v1`：视频，以 aid 为身份，BVID 为已知别名。
- `extensions.bilibili.membership.v1`：收藏条目的可用性、收藏时间和本次页面观察位置。

图关系为 `账号 —owns→ 收藏夹 —contains→ 收藏条目 —item→ 视频 —author→ UP 主`。同一视频可由多个收藏条目引用。成员记录中的页码和页内位置是采集观察事实，不承诺原站的实时绝对顺序。

本 Distribution 不注册 Source，不下载视频二进制、字幕、封面或其他远程资料，不恢复失效视频，也不修改源站收藏。封面 URL 仅作为已保存事实被解释。原生身份去重与批次状态原子提交由采集端和数据库协议完成。

## 兼容与验证

Host SDK 为 `core-py >=0.3.4 <0.4.0`；该版本来自 Python App 的 Host SDK 版本，区别于独立 `inkcre-core` SDK 项目的版本号。

```sh
pdm run python -m pytest -q tests/extensions/bilibili
pdm run python -m build --wheel extensions/bilibili
```

单元测试使用与 Web v1 形状一致的虚构资料，并调用现有词法投影入口。真实浏览器采集、数据库持久化和跨 Peer 检索由主任务验收记录报告。
