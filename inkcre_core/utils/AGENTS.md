# Utilities

> Applies to `utils/`.

- Utilities must remain domain-agnostic and must not import application or business policy.
- Prefer an existing domain owner over adding a generic helper that obscures semantics.
- Export only frequently used stable items from `utils/__init__.py`.
- Required check: run affected tests and import-boundary checks.
