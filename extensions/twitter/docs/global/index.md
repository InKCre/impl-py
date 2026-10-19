# 用 x-monkey 将 X 帖子保存到 InKCre

`inkcre/twitter` 可以接收原版 [x-monkey](https://github.com/steven-vvv/x-monkey)
捕获的帖子、作者和媒体元数据。保存后，关闭脚本仍可在 InKCre 中检索和阅读。它与 Bookmark 采集共用原生帖子身份，不需要为 x-monkey 配置 X
OAuth 应用。

本文是 Twitter
Extension 源码中的用户指南，尚未构建或发布为 Registry 托管文档站。截图来自隔离预览，展示实际操作界面；请使用自己部署中显示的连接地址。已验证的候选源码版本为 Python
`0.4.1` 与 Web Module Federation `0.4.2`，这些数字不表示相同版本的公开产物已经包含本功能。

## 准备连接

你需要已连接自己 info-base 的 InKCre
Web、可从浏览器访问的 Core 公共 HTTPS 地址，以及正在使用的原版 x-monkey 脚本。本次接入以 x-monkey
`v0.2.0` 为兼容基线。

在 Core 与 Web Peer 分别安装、启用兼容的 `inkcre/twitter`
Distribution。Core 运行 Python 分发，Web 运行 Module
Federation 分发；两者独立选择版本，不要求版本数字相同。此功能的 Python 分发要求 Core Host SDK
`>=0.3.1 <0.4.0`，Web 分发也需满足其宿主兼容范围。

1. 在 InKCre Web 的 **Extensions** 中打开 Twitter 的 **Setup**，选择连接同一 info-base 的在线 Core
   Peer。如果尚未启用，先在 Core 和 **This browser** 启用相应分发。
2. 找到 **Connect x-monkey storage**，复制其中的 **x-monkey service address**，再点击 **Connect
   x-monkey**。
3. 浏览器进入该 Core 的账户页，确认已连接的 Peer 和会话到期时间。这里使用现有 InKCre 连接建立会话，不需要把
   `JWT_SECRET` 交给脚本或 X。
4. 返回 x-monkey，打开 **设置 → 远端数据库**，启用远端数据库，将刚才的服务地址填入
   **服务地址**，保存并点击
   **检测**。确认显示已连接，再开始同步。地址必须保留 Setup 显示的完整路径前缀。

如果脚本先显示“需要登录”，可点击“打开账户页”查看连接指引，再从已连接的 InKCre
Web 完成上述操作，最后回到脚本重新检测。账户页不自动查找你的 InKCre
Web 地址。Setup 没有连接区时，检查所选 Core 及两端分发是否支持本功能。

此会话具有现有 Peer
JWT 的完整信息库访问权限，默认有效期为 7 天。有效的会话检测、查询和提交请求进入配置寿命的后一半时自动续签；部署者可以调整这个签发期限。过期后需重新从 Web 连接，不需要额外的 refresh
token。账户页的断开操作只清除此浏览器的会话，不删除已保存信息，也不撤销已经复制的 JWT。

## 同步已捕获的帖子

在 X 打开帖子，让 x-monkey 捕获它，再在脚本中打开该帖详情。找到 **远端归档**，点击
**同步此帖**，检查帖子、作者及媒体的逐项结果。**同步全部**也可能包含已经捕获的上下文；它不会让 InKCre 补抓尚未捕获的完整线程或历史。

![原版 x-monkey 显示帖子、作者和媒体元数据同步成功](images/x-monkey-sync.jpg)

图中是预览环境的真实同步结果，记录数量取决于脚本捕获的内容。“媒体已同步”表示媒体元数据已保存，不表示图片或视频文件已经转存。

再次同步同一原生帖子会补充同一个信息根，保留已知字段，并按计数观察时间合并更新。Bookmark 采集到同一帖子时也补充这份信息，而不是另建供脚本使用的归档副本。

## 在 InKCre 中找回和阅读

按既有的[索引与搜索流程](https://inkcre.dev/guide/search)维护词法索引，再用原帖 ID、作者或正文线索查找。在 Info
Base 打开结果，即可阅读已捕获的正文、长文、原帖链接和上下文；这时可以关闭 x-monkey。

需要查看图片或视频时，点击对应媒体的 **Load
media**。只有这个显式操作才从已捕获的 CDN 地址读取媒体字节，保存、查询、列表、正文投影和索引不会预先下载它们。

![同一帖子在 InKCre 中打开，显示原帖链接、正文与显式加载的照片](images/x-monkey-read.jpg)

图中照片是在 InKCre 里点击加载后显示的。本版本保存原生媒体元数据，不持久化媒体字节；**Load media**
也不执行转存。如果源站 URL 失效、读取超时或文件超限，界面会反馈失败，文字和元数据仍保留。不要把“已同步”当作媒体文件已长期保全。

## 与 Bookmark 授权分别管理

x-monkey 使用现有 Peer JWT 连接自己的 InKCre。Bookmark 采集仍使用 Twitter Setup 中单独配置的 X
OAuth 应用与账号授权，随后通过 Source 和 Job 执行；两种连接不互为前提。连接 x-monkey 不需要创建 Bookmark
Source，也不会替你启动书签采集。

需要自动采集自己的书签时，继续使用现有的
[Bookmark 操作指南](https://inkcre.dev/guide/sources/twitter)。断开脚本会话或关闭脚本不会删除两种入口已经保存的信息。
