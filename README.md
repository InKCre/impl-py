# InKCre Python Implementation

The Python peer of InKCre. Owns the authoritative database schema and migrations, hosts the `inkcre-cli`, and ships Python-implemented first-party extensions.

`impl-py` 是 InKCre 的 Python 实现——一个持有数据库 schema 权威的 peer，同时承载命令行工具和一方 Python 扩展。

## Quickstart

### Self-host an InKCre instance

You do not need to clone this repository. Fork it on GitHub, configure the documented secrets, and run the checked-in self-host workflow:

- [Render + Neon](docs/40-deployment/render-neon-self-host.md)
- [Heroku + Neon](docs/40-deployment/heroku-neon-self-host.md)

### Develop locally

```bash
git submodule update --init --recursive
pdm install -G app -G dev --frozen-lockfile
svc dev ensure database --repo . --json
pdm run dev
```

The full validation gate used by CI is `pdm run check`. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, migrations, and delivery details.

## Documentation

- [Documentation index](docs/index.md)
- [Agent operating guide](AGENTS.md)
- [Deployment and runtime truth](docs/40-deployment/README.md)
- [Local unit design](docs/30-unit-tdd/README.md)
- Shared product truth Hub: [`InKCre/docs`](https://github.com/InKCre/docs) (mounted read-only at `docs/_shared/`)

## Security

Report vulnerabilities privately through [SECURITY.md](SECURITY.md). Security design follows the shared [Security Boundary Model](docs/_shared/20-product-tdd/security-boundary-model.md) and the local [runtime projection](docs/30-unit-tdd/security-model.md).

## Generated artifacts

- OpenAPI schema: `docs/openapi.json` (regenerate with `pdm run python scripts/generate-openapi.py`)
- This repository does not publish hosted API documentation automatically

## 独立 Python SDK

构建 `pdm build` 得到 `inkcre-core` wheel，安装后使用 `inkcre_core`。基础依赖不包含 FastAPI、Uvicorn、APScheduler 或扩展安装器；服务开发使用 `pdm install -G app -G dev`。Core 当前支持 Python 3.12、PostgreSQL/psycopg，数据库 schema 和迁移仍由本仓 App 运维入口管理。

```python
from inkcre_core import Core
from inkcre_core.business.info_base import BlockService

async def read_block(database_url: str, block_id: int):
    core = Core(database_url)
    try:
        with core.bind():
            return await BlockService.get(block_id)
    finally:
        await core.aclose()
```

`Core` 构造不连接数据库或启动执行者。调用方传入数据库 URL 或借用的 `AsyncEngine`，用 `CoreOptions` 提供 Peer 身份、认证和调试参数。绑定按异步任务隔离，子任务继承；每次领域操作仍建立自己的短事务。A、B 实例可以并行操作不同信息库，应用切换配置不改变既有实例。关闭会收束该实例的 Job，释放自建 engine；借用 engine 的关闭权仍属于调用方。调用方应先结束自己的操作再关闭实例。

公开消费面是 `Core` / `CoreOptions`、`inkcre_core.schemas` 的模型，以及各 `inkcre_core.business` 包导出的领域入口（例如 `BlockService`、`submit_graph`）。`persistence` 和私有实现模块属于内部细节。普通读写不需要启动 Job、恢复扩展或加载 `app.settings`；使用 Resolver 时可显式注册所需实现。Job 的启动和轮询由调用方决定。

HTTP 路由、环境读取、调度频率、扩展安装／进程重启和 Sink 的服务挂载由 `app/` 保留。已有独立 Extension runtime 的生命周期合同保持原 owner。原 `app.business.*`、`app.schemas.*`、`libs.*`、`utils.*` 导入作为服务的兼容入口保留，历史迁移与已发布扩展仍指向同一实现；独立 wheel 只交付 `inkcre_core`，不交付这些旧服务路径。

本地边界检查：`pdm run check:core-package` 构建 wheel 并在仓库之外检查独立消费，`pdm run python -m pytest -q tests/test_core_context.py` 验证并行绑定与资源所有权；两者均不连接数据库，不属于端到端验收。
