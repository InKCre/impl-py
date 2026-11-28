"""Hermetic process environment for the repository test suite."""

import asyncio
import os
from pathlib import Path
import runpy
import subprocess
import sys

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


@pytest.fixture(scope="session")
def migrated_database_template():
  """Migrate an empty database once for scenarios that scan an entire info-base."""
  from sqlalchemy.engine import make_url
  from tests.database import temporary_database

  owner = os.environ.get("INKCRE_TEST_POSTGRES_ADMIN_URL")
  if not owner:
    pytest.skip("requires a disposable PostgreSQL admin URL for a private info-base")
  with temporary_database(owner) as template:
    url = make_url(owner).set(database=template).render_as_string(hide_password=False)
    subprocess.run(
      [sys.executable, "scripts/database.py", "init", "--profile", "runtime"],
      cwd=Path(__file__).resolve().parents[1],
      env={**os.environ, "MIGRATION_DATABASE_URL": url, "DATABASE_URL": url},
      check=True,
    )
    yield owner, template


@pytest.fixture
def isolated_database_runtime(
  migrated_database_template, monkeypatch, application_core_scope
):
  """Run a whole-info-base scenario on a fresh copy using the ordinary Core role."""
  import sqlalchemy
  from sqlalchemy.ext.asyncio import create_async_engine
  from sqlalchemy.pool import NullPool

  from app import core as application, engine
  from inkcre_core import Core
  from inkcre_core.business.job import JobManager
  from tests import database

  owner, template = migrated_database_template
  with database.temporary_database(owner, template=template) as name:
    url = database.TEST_ENGINE.url.set(database=name)
    sync_engine = sqlalchemy.create_engine(url, poolclass=NullPool)
    # Jobs use the test loop, while ASGI clients can enter on a separate loop.
    async_engine = create_async_engine(url, poolclass=NullPool)
    core = Core(async_engine, options=application_core_scope.options)
    monkeypatch.setattr(database, "TEST_ENGINE", sync_engine)
    monkeypatch.setattr(application, "CORE", core)
    monkeypatch.setattr(engine, "ASYNC_DB_ENGINE", async_engine)
    with core.bind():
      JobManager.start()
      try:
        yield
      finally:
        asyncio.run(core.aclose())
        asyncio.run(async_engine.dispose())
        sync_engine.dispose()
