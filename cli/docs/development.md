# 开发与发布

遵循仓库的 [贡献流程](../../CONTRIBUTING.md)、组织 [Git workflow](https://github.com/InKCre/.github/blob/main/GOVERNANCE.md)
和 [验证策略](https://github.com/InKCre/.github/blob/main/TESTING.md)。手工/脚本端到端验收不默认成为 CI 自动化。

CLI 使用独立 PDM project，不是 root workspace 成员；不要添加 Core path dependency。
从仓库根运行声明的脚本会进入子项目：`pdm install -p cli --frozen-lockfile`、`pdm run -p cli check`、
`pdm build -p cli`。直接传给 `pdm run` 的任意命令则要注意工作目录，不用 `ruff check src` 误查根目录。

运行边界是 Click command → CoreRESTClient → 普通 REST。命令直接映射协议，不另加逐 endpoint 转发 service。
CLI 使用 Pydantic 验证本机连接和自身 JSON 输入；可信 REST 返回只解码/投影，不做响应 model_validate。
schema 查询取得 Core OpenAPI 与运行时 owner schema，机械调整输入位置和局部引用，不编译 Extension 模型。

同步 HTTPX 处理普通请求；有界 Job/wake 观察使用一个原生 asyncio timeout 作用域和 AsyncClient，以总预算
覆盖慢响应。HTTPX 单阶段 timeout 不是总期限。取消这个作用域关闭真实请求，不留下后台请求线程。

## 版本与 PyPI

Feature PR 只放 `cli/.changes/` fragment，使用根 Towncrier 类型。单一 `release/next` PR 消费 CLI intent，
修改 CLI 自己的 version/changelog；CLI 不进入 Extension matrix。0.0.0 是初始开发版本，added fragment
经过正常准备得到 0.1.0；不能因为 base 中尚无 CLI 就直接发布。

`cli-publish.yml` 从精确 main SHA 自行检查、构建 wheel/sdist，并通过 PDM 原生 Trusted Publishing 上传。
Configure the PyPI pending publisher with owner `InKCre`, repository `impl-py`, workflow
`cli-publish.yml`, and environment `production`.
安装者只需 pip，不需 PDM。新增 REST 依赖时先交付 Core provider，再发布 CLI consumer；没有永久版本相等 gate。

恢复使用 main 上的 workflow_dispatch。PDM `--no-build --skip-existing` 保留当前 run 已检查的构建并续传缺失
文件；重跑会重新构建，不声称跨 run byte-identical。不要手写 OIDC 换 token 或重新使用 CI 上传的产物。
