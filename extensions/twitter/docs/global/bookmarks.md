---
description: 授权自己的 X 账号，创建 Bookmark Source 并观察一次采集。
outline: false
---

# 授权与采集 Bookmark

Bookmark 采集使用 X 官方 API 读取自己账号的书签，与
[x-monkey 连接](./x-monkey.md)独立。你需要可访问相关 API 的 X 开发者应用，以及一条能辨认正文的近期书签。开始前检查应用的访问权限和计费；具体条件以
[X Bookmarks 文档](https://docs.x.com/x-api/posts/bookmarks/introduction)为准。

这条路径使用 OAuth 2.0 用户授权。应用级 app-only bearer token 不能代替账号授权；不要把 InKCre 的
`JWT_SECRET` 填入 X。

<InterfaceGuide>
<template #web>

## 在 Web 中连接 X 账号

1. 在所选 Core 和 **This browser** 安装、启用兼容的 `inkcre/twitter` 分发。在 **Extensions**
   打开 Twitter 的 **Setup**，选择在线 Core；仅启用浏览器分发不能执行采集。
2. 复制 Setup 显示的 callback URL。在 X 开发者应用的 OAuth 2.0 设置中选择提供 Client ID 和 Client
   Secret 的 confidential web-app 客户端，并完整登记该回调地址。
3. 在 Setup 输入 OAuth **Client ID** 和 **Client Secret**，不是 API Key/Secret。点击 **Save OAuth
   App**；部分界面显示 **Save application**。
4. 点击 **Create authorization link**，再打开 **Open X
   authorization**。批准要采集的账号，返回向导等待状态更新，并核对连接的 handle。一次授权需在十分钟内完成。

扩展请求 `tweet.read`、`users.read`、`bookmark.read` 和
`offline.access`；后者用于刷新 X 的账号授权。这些 X 凭据与 x-monkey 的 Peer
JWT 会话是两套用途不同的授权。X 的具体设置见
[OAuth 2.0 PKCE 说明](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)。

所有 Twitter Bookmark
Sources 共用扩展当前连接的一个 X 账号。不要通过切换账号来创建另一个人的 Source；那会改变既有 Sources 使用的账号。替换 OAuth 应用可能断开当前账号，先阅读重置确认。

## 设置并观察采集

1. 选择已有 **Bookmark Source**，或输入昵称后点击 **Create Bookmark
   Source**。已有目标 Source 时不要再创建一份。
2. 设置 **Collect bookmarks daily at**，再点击
   **Continue**。此时间交给 Core 的调度器解释，部署时区应由运行人员确认，不应仅按浏览器时区理解。
3. 复核账号、Source 与时间，明确点击 **Start collecting
   bookmarks**。它同时启用计划并提交一次立即采集 Job。
4. 打开该 Source 的 Jobs，等待这次 Job 的实际结果，再按[检索与阅读](./reading.md)维护索引、搜索那条已知书签。点击启动并不表示采集已完成；不要再为同一 Source 添加第二份计划。

</template>
<template #cli>

## 通过 CLI 授权和采集

这条路径不需要安装 Web Module Federation 分发。先将
[CLI 连接到目标 Core](https://inkcre.dev/guide/connect-cli)，确认该 Core 上的 Twitter
Python 分发已启用，并在 `inkcre-cli source types` 中看到 `extensions.twitter.bookmark.Source`。

### 1. 授权自己的 X 账号

CLI 当前没有专用 OAuth 设置命令，下面的本地脚本直接调用 Core 已有的 setup
API。使用“连接 CLI”步骤中的 Python 环境；`inkcre-cli` 的依赖已经提供 `httpx` 和
`PyJWT`，脚本不需要 Web 分发。授权属于整个扩展，所有 Bookmark
Sources 共用当前账号，不要把 OAuth 配置写进 Source。

保存为 `connect-twitter.py`，在该 Python 环境中运行
`python connect-twitter.py`。脚本先显示确切的回调地址；保持脚本运行，在 X 开发者应用中启用 OAuth
2.0，选择提供 Client ID/Secret 的 confidential web-app 客户端，完整登记该地址，再继续。使用 OAuth
Client ID/Secret，不是 API Key/Secret；设置说明见
[X OAuth 2.0 文档](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)。

```python
import getpass
import time

import httpx
import jwt

core_url = input("Core HTTPS URL: ").strip().rstrip("/")
if not core_url.startswith("https://"):
    raise ValueError("Use your trusted Core HTTPS URL, not PostgREST")
secret = getpass.getpass("InKCre JWT_SECRET: ")
if not secret:
    raise ValueError("JWT_SECRET is required")

with httpx.Client(timeout=30) as client:
    def request(method, path, **kwargs):
        issued = int(time.time()) - 5
        token = jwt.encode(
            {"role": "authenticated", "iss": "inkcre-peer", "aud": "inkcre-api",
             "iat": issued, "exp": issued + 900},
            secret, algorithm="HS256",
        )
        response = client.request(
            method, core_url + path,
            headers={"Authorization": f"Bearer {token}"}, **kwargs,
        )
        response.raise_for_status()
        return response.json()

    status = request("GET", "/twitter/setup")
    print("Register this callback URL in X:", status["callback_url"])
    if not status["connected"]:
        client_id = getpass.getpass("X OAuth Client ID: ").strip()
        client_secret = getpass.getpass("X OAuth Client Secret: ")
        if not client_id or not client_secret:
            raise ValueError("Both OAuth app credentials are required")
        request("PUT", "/twitter/setup/oauth-app", json={
            "client_id": client_id, "client_secret": client_secret,
        })
        transaction = request("POST", "/twitter/setup/oauth-transactions")
        print("Open this private authorization URL:", transaction["authorize_url"])
        input("Authorize in X; after the callback says connected, press Enter: ")
        result = request("POST", "/twitter/setup/oauth-transaction", json={
            "transaction_id": transaction["id"],
        })
        if result["status"] != "succeeded":
            raise RuntimeError(f"Authorization did not complete: {result['status']}")
        status = request("GET", "/twitter/setup")
    if not status["connected"]:
        raise RuntimeError("Account is not connected; inspect setup status")
    print("Connected account:", status["handle"])
```

在 X 批准要采集的账号，十分钟内完成浏览器授权；脚本会检查事务结果和最终连接的 handle。`offline.access`
让扩展能够刷新 X 账号授权。凭据和脚本显示的授权链接都应保持私密，不要分享终端输出，也不要将
`JWT_SECRET` 输入 X 页面。账号 token 保存在部署中，可由该部署已准入的 Peers 使用。

回调地址缺失或不正确时，先修正 Core Peer 的 `http_public_base_url`，让它指向可公开访问的 Core
HTTPS 基址并产生
`/twitter/auth/callback`。新建授权会替代此前未完成的尝试。替换已配置的 OAuth 应用可能返回 HTTP
409；不要盲目添加 `confirm_account_reset` 重试，因为替换会断开现有账号并影响全部 Bookmark Sources。

### 2. 创建并采集 Source

保存 `twitter.json`：

```json
{
  "nickname": "我的 X 书签",
  "config": {}
}
```

```sh
inkcre-cli source create --type extensions.twitter.bookmark.Source --input twitter.json
```

记录返回的 Source ID，替换下面的 `42`：

```sh
inkcre-cli source collect 42 --input-json '{"result_limit":40}'
```

记录返回的 Job
ID，按既有[采集观察流程](https://inkcre.dev/guide/collect)确认完成，再[维护索引并阅读](./reading.md)。首次成功后，再按部署要求添加一个[采集计划](https://inkcre.dev/guide/schedules)。

</template>
</InterfaceGuide>

## 采集范围与停止方式

一次普通采集只读取一页，`result_limit`
为 5–100，默认 40；命中已记录的上一条主书签时，只保存它之前的新主书签，未命中则保存返回的本页。书签顺序变化或两次采集之间新增过多内容时，不能据此保证全部覆盖。

`full=true` 只取消本页的旧书签截断，并在 Job
state 记录下一页位置；当前实现不会自动提交下一页 Job 或排空历史，也没有独立的 Source
backfill 实现。不要把它当作完整回填、文件夹同步、取消书签同步或全部回复采集。

停止自动采集时先禁用对应 Cron，再在向导账号步骤选择
**Disconnect**。也可以在 X 撤销应用。它们都不删除已经保存的信息；断开 x-monkey 会话则不会代替停止 Bookmark 计划。

## 授权或采集失败时

- **setup 404**：检查 Core 地址及 Twitter 在该 Core 上的运行状态。
- **setup 401**：检查当前 InKCre Peer 连接；它与 X 的账号授权不同。
- **X 授权失效 / 要求重新连接**：重新授权，核对账号和应用是否被替换或撤销。
- **X 返回 402/403**：检查应用访问权限、额度和账号授权；**429** 表示应等待供应商限流恢复。
- **Job 完成但没有新信息**：核对连接的 handle，新增一条可辨认的书签后再运行，不重置已有 Source 游标来试错。

另有 `twikit` 账号登录后端；它不是上述 OAuth 向导流程。运行人员可从 Python
scope 查看实际配置和本地会话文件要求。

返回[扩展概览](./index.md)。
