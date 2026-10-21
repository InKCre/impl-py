---
description: 安装和运行 CalDAV Python Distribution。
---

# 在 Python Peer 上运行

选择 `inkcre/caldav` 的 Python Distribution，并在目标 Peer 上启用。该分发需要
Core Host SDK `>=0.3.2 <0.4.0`，使用此版本新增的共享事务锁与一致性读取能力。
扩展按标准 wheel 安装，直接依赖固定的 Radicale 3.8.3 和 vobject；不启动独立
Radicale 进程，不创建文件存储。

通过既有 Extension `patch_config` 设置 `password`，使用随机生成的 32–128 字符密码。
用户名固定为 `inkcre`。密码初值为空，尚不能认证；清空即撤销访问。每个请求读取当前
共享配置，因此密码轮换无需重启。密码不是 Peer JWT，也不应放入 URL 或日志。

使用 Core REST 管理接口时，可以按下面的顺序选择准确版本并启用。`CORE_URL` 是
目标 Peer 的 HTTPS 基址，`PEER_JWT` 是管理该部署的有效凭据；版本必须存在于该 Peer
配置的 Registry。首次下载 wheel 会暂停执行并重启 Python 进程，待 `/readyz` 恢复后
再执行后续步骤。

```sh
curl --fail-with-body "$CORE_URL/extensions/inkcre/caldav/selection" \
  -H "Authorization: Bearer $PEER_JWT" -H 'Content-Type: application/json' \
  -d '{"distribution":"python","version":"0.2.0"}'

curl --fail-with-body -X POST "$CORE_URL/extensions/inkcre/caldav/enable" \
  -H "Authorization: Bearer $PEER_JWT"
```

将专用密码保存到仅自己可读的 `caldav-config.json`，内容为 `{"password":"你的随机密码"}`，
然后写入配置。响应含扩展配置，应避免把它保存到共享日志。

```sh
curl --fail-with-body -X PATCH "$CORE_URL/extensions/inkcre/caldav/config" \
  -H "Authorization: Bearer $PEER_JWT" -H 'Content-Type: application/json' \
  --data-binary @caldav-config.json --output /dev/null

curl --fail-with-body "$CORE_URL/caldav/connection" \
  -H "Authorization: Bearer $PEER_JWT"
```

客户端服务地址为 Core 公共 HTTP 基址加 `/caldav/`。`caldav.connection.v1` 在运行时
发布 GET `/caldav/connection`，要求 Peer JWT，返回服务地址和用户名，不返回密码。
未配置公共 HTTP 基址时返回 409。此接口只提供连接信息，不证明手机能够访问。

反向代理必须转发 PROPFIND、REPORT、MKCOL、MKCALENDAR、PROPPATCH、MOVE、
PUT 和 DELETE 等 DAV 方法，保留 Depth、If-Match、If-None-Match、Destination、
Overwrite 头。有路径前缀时正确设置 ASGI root_path。客户端应填写完整服务地址；
部署根目录的 `.well-known/caldav` 和 `.well-known/carddav` 重定向由部署方按需配置。

请求体上限为 16 MiB，单个资源上限为 8 MiB。修改已有资源必须使用当前 ETag。
集合整体 PUT 覆盖被禁止，以避免错误导入覆盖整个集合。集合删除仍可通过标准 DAV
操作执行，会删除其中资源，不能当作取消订阅使用。

同步令牌代表当前内容；内容变化后客户端重新枚举集合。当前实现适用于个人信息库，
每次事务读取此扩展的图，写事务串行处理。没有增量变更日志，也未承诺大型组织通讯录吞吐。
备份和恢复沿用 InKCre 数据库；不需要另行备份 DAV 文件目录。

本地手机验收可以通过 `adb reverse` 将手机回环端口转发到开发服务。此方式依赖 USB
连接及开发进程存活，不能替代可持续使用的 HTTPS 部署。

## 共享内容与升级

0.2.0 在启用时将旧资源原子迁移为 v2 图模型，保留根对象 ID、凭据与外部引用。电话、邮箱、正文和媒体成为普通内容 Block；其他 Peer 的修改会进入手机同步。手机换值会改为引用新内容，不覆盖其他图引用的旧值。

在 DAV 客户端删除资源是撤下同步发布，信息库对象仍保留；需要彻底删除时须在信息库中明确删除对象。升级可能使 ETag 改变并触发重新同步，不支持回退到只理解 v1 的旧分发。
