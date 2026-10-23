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
pdm install -G dev --frozen-lockfile
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

## Acknowledgements

内容提取工作受 [Hister](https://github.com/asciimoo/hister) 启发。感谢 asciimoo 与贡献者；本功能未复制其源码或资源。
具体借鉴范围见 [Organization](docs/30-unit-tdd/organization.md)。

## Security

Report vulnerabilities privately through [SECURITY.md](SECURITY.md). Security design follows the shared [Security Boundary Model](docs/_shared/20-product-tdd/security-boundary-model.md) and the local [runtime projection](docs/30-unit-tdd/security-model.md).

## Generated artifacts

- OpenAPI schema: `docs/openapi.json` (regenerate with `pdm run python scripts/generate-openapi.py`)
- This repository does not publish hosted API documentation automatically
