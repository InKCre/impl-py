- Import your schema in `inkcre_core/schemas/__init__.py` so Alembic discovers it while generating migrations.
- Follow the [Python input/restoration boundary](../../docs/30-unit-tdd/business-pipeline-and-authority.md#cross-subtree-constraints)
  when changing Pydantic forms or persistence codecs. A typed input should not be dumped and revalidated merely to cross
  an internal call boundary; necessary restoration of nested persisted types is a different operation.
