# Core-Py Unit Design

This directory owns expensive internal design truth for logical units delivered by this repository. Code, schemas, tests, assertions, and automation remain authoritative when they can prevent drift directly. Shared Product behavior and cross-unit contracts remain read-only Hub truth under `../_shared/`.

| Document | Local owner and admission |
| --- | --- |
| [business-pipeline-and-authority.md](business-pipeline-and-authority.md) | Stable authority and dependency direction across core-py business units |
| [semantic-retrieval.md](semantic-retrieval.md) | Internal projection, maintenance, ranking, and Agent composition mechanics |
| [lexical-retrieval.md](lexical-retrieval.md) | Internal lexical projection, feature extraction, maintenance, and ranking mechanics |
| [graph-navigation-retrieval.md](graph-navigation-retrieval.md) | Internal bounded neighborhood, path, endpoint-closure, and query mechanics |
| [mcp-sink.md](mcp-sink.md) | Persisted Sink lifecycle, MCP projection, Resource and Resolver-method mechanics |
| [agent-query-sink.md](agent-query-sink.md) | Asynchronous Agent-composed query lifecycle, result delivery, and setup contract |
| [mail-extension.md](mail-extension.md) | Mail identity, MIME materialization, collection, graph, and failure boundaries |
| [memos-extension.md](memos-extension.md) | Memos adapter identity, graph grammar, persistence, and failure boundaries |
| [capture-extension.md](capture-extension.md) | Capture submission identity, original materials, shared graph, and atomic recovery |
| [rss-extension.md](rss-extension.md) | RSS adapter identity, collection lifecycle, reconciliation, and materialization |
| [twitter-extension.md](twitter-extension.md) | Twitter 共同身份、Bookmark 过渡、事务合并和按需媒体读取 |
| [security-model.md](security-model.md) | Core-py realization of the shared security boundary model |

Do not add a document for a directory inventory, a fact cheaply recovered from code, a one-off decision, or a cross-unit contract already owned by `../_shared/20-product-tdd/`. Put repeated physical-subtree hazards in the nearest `AGENTS.md`; put runtime, rollout, recovery, and environment truth in `../40-deployment/`.
