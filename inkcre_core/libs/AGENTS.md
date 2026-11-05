# Shared Libraries

> Applies to `libs/`.

- Libraries must not acquire business-domain authority or import `app/business` policy.
- Observability code may transport and format telemetry; it must not become the authority for application state.
- AI contracts belong in `inkcre_core/schemas/ai/` and execution in `inkcre_core/business/ai/`; do not reintroduce a process-global AI client under `libs/`.
- Required check: run the affected library tests and import-boundary checks.
