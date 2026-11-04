# Service Application

> Applies to `app/`; deeper guides add narrower constraints.

- Authority and dependency direction are documented in `docs/30-unit-tdd/business-pipeline-and-authority.md`; do not reconstruct them from directory names.
- Settings enter through `app/settings.py`; `app/core.py` composes and binds the service-owned SDK instance. `app/engine.py` only preserves old service imports. Domain operations use the bound Core and own short UoWs.
- Runtime registration and database effects belong in lifespan/bootstrap paths, not module import. Preserve import-only OpenAPI generation.
- Routes parse transport input and call owning business managers; do not duplicate business policy in routes.
- Required check: run the narrow tests for the changed unit, then `pdm run check:foundation` for bootstrap, dependency, or import-boundary changes.
