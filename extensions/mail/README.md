# Mail Extension

Mail 通过 IMAP 采集邮件，保留正文、参与者、邮箱归属、标记、回复关系和附件元数据。
普通采集面向 Source 创建之后的邮件；历史邮件使用显式 backfill Job 导入。

带有桌面端实际截图的操作说明见 [Newsletter 阅读指南](docs/global/index.md)。
指南展示尚未发布的 newsletter 增强功能。

创建类型为 `extensions.mail.source.Source` 的 Source，配置示例：

```json
{
  "protocol": "imap",
  "parameters": {
    "host": "imap.example.org",
    "port": 993,
    "security": "tls",
    "username": "reader@example.org",
    "password": "application-password"
  },
  "ordinary_mark_as_seen": true,
  "backfill_mark_as_seen": false,
  "synchronize_deletions": false
}
```

Source 支持普通 collect/backfill Job 与 Cron；无需 Mail 专用创建接口。
默认排除 Drafts、Junk、Trash，可用 `excluded_mailboxes` 指定邮箱名称与 special-use 排除项。
附件和内嵌图片默认只采集元数据，用户显式读取时才获取内容。

具有有效 `List-ID` 的邮件会归入同一刊物；没有该头时，也支持 Beehiiv 同时提供的
`X-Beehiiv-Type: newsletter` 和有效 `X-Newsletter-ID` 刊物 URL，例如 Python Weekly。
newsletter 阅读视图清理隐藏预览文字、
跟踪像素、明确的订阅操作链接和已知跟踪参数，保留文章结构及功能性链接参数。
可随时查看原文；两种视图均不被动加载外部图片。

在邮件中点击刊物名称可查看各期。重复投递折叠展示，各副本仍可打开；
不会凭相同主题合并不同期，也不执行自动退订。缺少受支持刊物声明的邮件保留普通处理，
不会根据发件地址或退订链接猜测刊物。
升级前已采集的邮件需通过 backfill 重新读取相应日期范围，才会补齐刊物归属与正文投影。
使用此功能需同时更新 Python 和网页端 Mail Distribution。

桌面阅读提供左侧目录、标题跳转及前后导航，正文使用宿主的一层滚动。
重新打开同一篇正文时可继续上次阅读，位置仅保存在当前浏览器。窄窗口默认收起目录。
搜索结果将同封 newsletter 的正文和邮件命中汇入同一阅读入口，详情仍可检查原始命中。
HTML 搜索摘要使用可见文字，去掉布局表格产生的 Markdown 和属性中的跳转地址；
已有索引需显式 lexical rebuild 才会采用新投影，阅读和原文中的有效链接继续保留。

完整数据与行为合同见 [Mail extension 设计](../../docs/30-unit-tdd/mail-extension.md)。
