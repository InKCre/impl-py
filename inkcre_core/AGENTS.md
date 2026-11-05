# Independent Python SDK

- Ordinary SDK imports and operations must not depend on `app`, FastAPI, Starlette, the service scheduler, environment loading, or an application singleton.
- `Core` owns its engine factory and task-local binding. Each operation still creates its own UoW; never put a live session in the context or share it across concurrent tasks.
- Keep shared protocol IDs, model fields and transaction guarantees stable when moving modules. Legacy service aliases must resolve to the same model/service objects.
- Consumer entrypoints and ownership are documented in the repository README. Do not expose persistence internals as a stable public facade merely because they are packaged.
- Check the built wheel with `pdm run check:core-package`; it is consumed outside this checkout with application imports rejected. This check does not contact a database.
