"""Hermetic process environment for the repository test suite."""

import asyncio
import os
from pathlib import Path
import runpy

import pytest


database_url = os.environ.get(
  "INKCRE_TEST_DATABASE_URL",
  "postgresql+psycopg://test:test@127.0.0.1:1/test",
)

os.environ.update(
  {
    "INKCRE_ENV_FILE": "",
    "DATABASE_URL": database_url,
    "JWT_SECRET": "test-only-jwt-secret-at-least-32-bytes",
    "OBSRV__LOGGING_BACKEND": "none",
    "SKIP_EXTENSION_START": "1",
  }
)


@pytest.fixture(scope="session")
def semantic_content_assets() -> Path:
  """Generate ignored real-format samples only when a test module needs them."""
  asset_directory = Path(__file__).parent / "assets" / "semantic-content"
  namespace = runpy.run_path(
    str(asset_directory / "generate_assets.py"),
    run_name="semantic_content_asset_generator",
  )
  generate = namespace["main"]
  if not callable(generate):
    raise RuntimeError("semantic-content asset generator has no callable main")
  generate()
  return asset_directory


@pytest.fixture
def async_runner():
  from app.engine import ASYNC_DB_ENGINE

  # Keep the pooled connections on their owning loop, including disposal.
  with asyncio.Runner() as runner:
    try:
      yield runner
    finally:
      runner.run(ASYNC_DB_ENGINE.dispose())


@pytest.fixture(autouse=True)
def application_core_scope():
  from app.core import CORE

  with CORE.bind():
    yield CORE
