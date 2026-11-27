"""Disposable PostgreSQL proof for Peer-scoped Extension participation v2."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
import uuid

import psycopg
from psycopg import conninfo, sql
import pytest
import sqlalchemy
import sqlalchemy.exc
import sqlmodel

from app.database_contract.connection import psycopg_database_url
from inkcre_core import Core
from inkcre_core.business.cron import CronManager
from app.business.extension.errors import ExtensionStateConflictError
from app.business.extension.state import ExtensionStateService
from inkcre_core.business.job import JobManager
from inkcre_core.business.source import SOURCE_COLLECT_JOB_TYPE, SourceManager
from inkcre_core.schemas.extension import ExtensionModel, ExtensionPeerParticipationModel
from inkcre_core.schemas.peer import PeerModel
from inkcre_core.schemas.cron import CronForm
from extensions.twitter.bookmark import Source as BookmarkSource


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def postgres_connection_info() -> dict[str, Any]:
  owner_url = os.getenv("INKCRE_TEST_POSTGRES_ADMIN_URL")
  if owner_url is None:
    pytest.skip("requires an explicitly selected disposable PostgreSQL admin URL")
  return conninfo.conninfo_to_dict(psycopg_database_url(owner_url))


@pytest.fixture(scope="module")
def participation_databases(postgres_connection_info):
  """Create and retire only this module's databases on the disposable cluster."""
  databases: list[str] = []

  def create() -> str:
    database = f"participation_{uuid.uuid4().hex}"
    with psycopg.connect(**postgres_connection_info, autocommit=True) as connection:
      connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    databases.append(database)
    return database

  try:
    yield create
  finally:
    with psycopg.connect(**postgres_connection_info, autocommit=True) as connection:
      for database in databases:
        connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))


def _environment(database_url: str) -> dict[str, str]:
  return {
    **os.environ,
    "INKCRE_ENV_FILE": "",
    "DATABASE_URL": database_url,
    "MIGRATION_DATABASE_URL": database_url,
  }


def _database_url(info: dict[str, Any], database: str) -> str:
  return sqlalchemy.URL.create(
    "postgresql+psycopg",
    username=info.get("user"),
    password=info.get("password"),
    host=info.get("host"),
    port=int(info["port"]) if "port" in info else None,
    database=database,
  ).render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def setup_domain_engine(postgres_connection_info, participation_databases):
  database = participation_databases()
  database_url = _database_url(postgres_connection_info, database)
  migrated = subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    cwd=PROJECT_ROOT,
    env=_environment(database_url),
    check=False,
    capture_output=True,
    text=True,
  )
  assert migrated.returncode == 0, migrated.stderr
  provisioned = subprocess.run(
    [sys.executable, "scripts/database.py", "provision-roles"],
    cwd=PROJECT_ROOT,
    env=_environment(database_url),
    check=False,
    capture_output=True,
    text=True,
  )
  assert provisioned.returncode == 0, provisioned.stderr
  engine = sqlalchemy.create_engine(database_url)
  try:
    yield engine
  finally:
    engine.dispose()


@pytest.fixture
def domain_runtime(setup_domain_engine):
  core = Core(setup_domain_engine.url.render_as_string(hide_password=False))
  with core.bind(), asyncio.Runner() as runner:
    try:
      yield runner
    finally:
      runner.run(core.aclose())


@pytest.fixture(scope="module")
def legacy_migration_database(postgres_connection_info, participation_databases):
  database = participation_databases()
  database_url = _database_url(postgres_connection_info, database)
  environment = _environment(database_url)
  migrated = subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "3d9593b0c855"],
    cwd=PROJECT_ROOT,
    env=environment,
    check=False,
    capture_output=True,
    text=True,
  )
  assert migrated.returncode == 0, migrated.stderr
  peer_id = uuid.uuid4()
  with psycopg.connect(**{**postgres_connection_info, "dbname": database}) as connection:
    connection.execute(
      "INSERT INTO inkcre.peers (id, name, config_schema) "
      "VALUES (%s, 'legacy-peer', '{}'::jsonb)",
      (peer_id,),
    )
    connection.execute(
      "INSERT INTO inkcre.extensions "
      "(name, version, enabled, config, state, config_schema) "
      "VALUES ('inkcre/legacy', '0.7.4', '{}'::uuid[], "
      "'{\"shared\":true}'::jsonb, '{}'::jsonb, "
      '\'{"type":"object"}\'::jsonb)'
    )
    connection.execute(
      "UPDATE inkcre.extensions SET state = '{\"progress\":4}'::jsonb "
      "WHERE name = 'inkcre/legacy'"
    )
    connection.execute(
      "UPDATE inkcre.extensions SET enabled = ARRAY[%s]::uuid[] "
      "WHERE name = 'inkcre/legacy'",
      (peer_id,),
    )
    connection.execute(
      "INSERT INTO inkcre.extensions (name, version, enabled) "
      "VALUES ('inkcre/disabled', '0.2.0', '{}'::uuid[])"
    )
  yield postgres_connection_info, database, database_url, peer_id, environment


def test_upgrade_preserves_shared_state_and_exact_unresolved_legacy_pin(
  legacy_migration_database,
):
  info, database, database_url, peer_id, environment = legacy_migration_database
  migrated = subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    cwd=PROJECT_ROOT,
    env=environment,
    check=False,
    capture_output=True,
    text=True,
  )
  assert migrated.returncode == 0, migrated.stderr
  provisioned = subprocess.run(
    [sys.executable, "scripts/database.py", "provision-roles"],
    cwd=PROJECT_ROOT,
    env=environment,
    check=False,
    capture_output=True,
    text=True,
  )
  assert provisioned.returncode == 0, provisioned.stderr
  with psycopg.connect(**{**info, "dbname": database}) as connection:
    participation = connection.execute(
      "SELECT distribution, version, enabled, config_schema "
      "FROM inkcre.extension_peer_participations "
      "WHERE name = 'inkcre/legacy' AND peer_id = %s",
      (peer_id,),
    ).fetchone()
    legacy_snapshot_schema = connection.execute(
      "SELECT config_schema FROM inkcre_internal.extension_legacy_snapshots "
      "WHERE name = 'inkcre/legacy'"
    ).fetchone()
    shared = connection.execute(
      "SELECT config, state FROM inkcre.extensions WHERE name = 'inkcre/legacy'"
    ).fetchone()
    disabled_rows = connection.execute(
      "SELECT count(*) FROM inkcre.extension_peer_participations "
      "WHERE name = 'inkcre/disabled'"
    ).fetchone()
  assert participation == (None, "0.7.4", True, None)
  assert legacy_snapshot_schema == ({"type": "object"},)
  assert shared == ({"shared": True}, {"progress": 4})
  assert disabled_rows == (0,)
  with psycopg.connect(**{**info, "dbname": database}, autocommit=True) as connection:
    connection.execute("SET ROLE authenticated")
    with pytest.raises(psycopg.errors.CheckViolation):
      connection.execute(
        "UPDATE inkcre.extension_peer_participations "
        'SET config_schema = \'{"type":"object"}\'::jsonb '
        "WHERE name = 'inkcre/legacy' AND peer_id = %s",
        (peer_id,),
      )


def test_enabled_legacy_resolution_preserves_exact_pin_and_null_schema(
  setup_domain_engine, domain_runtime
):
  peer_id = uuid.uuid4()
  name = f"inkcre/legacy-{peer_id.hex}"
  with sqlmodel.Session(setup_domain_engine) as session:
    session.add(PeerModel(id=peer_id, name="legacy-resolution-test"))
    session.add(
      ExtensionModel(
        name=name,
        config={"shared": True},
      )
    )
    session.flush()
    session.add(
      ExtensionPeerParticipationModel(
        name=name,
        peer_id=peer_id,
        distribution=None,
        version="0.7.4",
        enabled=True,
        config_schema=None,
      )
    )
    session.commit()
  with setup_domain_engine.begin() as connection:
    connection.exec_driver_sql(
      "UPDATE inkcre.extensions SET state = '{\"progress\":4}'::jsonb WHERE name = %s",
      (name,),
    )
    connection.exec_driver_sql(
      "INSERT INTO inkcre_internal.extension_legacy_snapshots "
      "(name, version, enabled, config_schema) "
      "VALUES (%s, '0.7.4', ARRAY[%s]::uuid[], %s)",
      (name, peer_id, '{"type":"object"}'),
    )

  store = ExtensionStateService()

  async def scenario():
    resolved = await store.resolve_legacy_python_participation(peer_id, name, "0.7.4")
    assert (resolved.distribution, resolved.version, resolved.enabled) == (
      "python",
      "0.7.4",
      True,
    )
    assert resolved.config_schema is None
    with setup_domain_engine.connect() as connection:
      assert connection.exec_driver_sql(
        "SELECT distribution, version, enabled, config_schema IS NULL "
        "FROM inkcre.extension_peer_participations "
        "WHERE name = %s AND peer_id = %s",
        (name, peer_id),
      ).one() == ("python", "0.7.4", True, True)
      assert connection.exec_driver_sql(
        "SELECT config, state FROM inkcre.extensions WHERE name = %s",
        (name,),
      ).one() == ({"shared": True}, {"progress": 4})
      assert connection.exec_driver_sql(
        "SELECT version, enabled, config_schema "
        "FROM inkcre_internal.extension_legacy_snapshots WHERE name = %s",
        (name,),
      ).one() == ("0.7.4", [peer_id], {"type": "object"})

    with pytest.raises(ExtensionStateConflictError, match="legacy selection changed"):
      await store.resolve_legacy_python_participation(peer_id, name, "0.7.3")
    with pytest.raises(ExtensionStateConflictError, match="Cannot change"):
      await store.select_participation(peer_id, name, "module_federation", "0.7.4")
    current = await store.get_participation(peer_id, name)
    assert current is not None
    assert (current.distribution, current.version, current.enabled) == (
      "python",
      "0.7.4",
      True,
    )

  domain_runtime.run(scenario())


def test_v2_acl_and_state_guards(setup_domain_engine, domain_runtime):
  peer_id = uuid.uuid4()
  name = f"inkcre/acl-{peer_id.hex}"
  with setup_domain_engine.connect().execution_options(
    isolation_level="AUTOCOMMIT"
  ) as connection:
    connection.exec_driver_sql(
      "INSERT INTO inkcre.peers (id, name, config_schema) "
      "VALUES (%s, 'participation-test', '{}'::jsonb)",
      (peer_id,),
    )
    connection.exec_driver_sql("INSERT INTO inkcre.extensions (name) VALUES (%s)", (name,))
    connection.exec_driver_sql("SET ROLE authenticated")
    with pytest.raises(
      sqlalchemy.exc.IntegrityError, match="state must be empty on insert"
    ) as rejected:
      connection.exec_driver_sql(
        "INSERT INTO inkcre.extensions (name, state) "
        "VALUES (%s, '{\"unsafe\":true}'::jsonb)",
        (f"inkcre/invalid-{peer_id.hex}",),
      )
    assert isinstance(rejected.value.orig, psycopg.errors.CheckViolation)
    connection.exec_driver_sql(
      "INSERT INTO inkcre.extension_peer_participations "
      "(name, peer_id, distribution, version, enabled) "
      "VALUES (%s, %s, 'python', '1.2.3', false)",
      (name, peer_id),
    )
    connection.exec_driver_sql(
      "UPDATE inkcre.extensions SET state = '{\"shared\":true}'::jsonb WHERE name = %s",
      (name,),
    )
    with pytest.raises(sqlalchemy.exc.IntegrityError, match="extensions_state_object"):
      connection.exec_driver_sql(
        "UPDATE inkcre.extensions SET state = '[]'::jsonb WHERE name = %s",
        (name,),
      )
    with pytest.raises(
      sqlalchemy.exc.ProgrammingError, match="permission denied"
    ) as denied:
      connection.exec_driver_sql(
        "UPDATE inkcre.extensions SET name = 'inkcre/renamed' WHERE name = %s",
        (name,),
      )
    assert isinstance(denied.value.orig, psycopg.errors.InsufficientPrivilege)
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql("SET ROLE anonymous")
    with pytest.raises(
      sqlalchemy.exc.ProgrammingError, match="permission denied"
    ) as denied:
      connection.exec_driver_sql(
        "UPDATE inkcre.extensions SET state = '{\"unsafe\":true}'::jsonb WHERE name = %s",
        (name,),
      )
    assert isinstance(denied.value.orig, psycopg.errors.InsufficientPrivilege)
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql("SET ROLE inkcre_core")
    connection.exec_driver_sql(
      "UPDATE inkcre.extensions SET state = '{\"ready\":true}'::jsonb WHERE name = %s",
      (name,),
    )
    connection.exec_driver_sql("RESET ROLE")
    with pytest.raises(
      sqlalchemy.exc.IntegrityError, match="state is not empty"
    ) as rejected:
      connection.exec_driver_sql("DELETE FROM inkcre.extensions WHERE name = %s", (name,))
    assert isinstance(rejected.value.orig, psycopg.errors.CheckViolation)

  async def verify_shared_state():
    store = ExtensionStateService()
    assert await store.read_state(name) == {"ready": True}
    await store.mutate_state(name, lambda state: {**state, "native": True})
    assert await store.read_state(name) == {"ready": True, "native": True}

  domain_runtime.run(verify_shared_state())


def test_peer_can_persist_its_own_distribution_format(setup_domain_engine, domain_runtime):
  peer_id = uuid.uuid4()
  name = f"inkcre/native-{peer_id.hex}"
  with sqlmodel.Session(setup_domain_engine) as session:
    session.add(PeerModel(id=peer_id, name="native-peer"))
    session.add(ExtensionModel(name=name))
    session.commit()

  async def scenario():
    store = ExtensionStateService()
    await store.select_participation(peer_id, name, "kotlin_apk", "0.1.0")
    await store.set_participation_enabled(peer_id, name, True)
    row = await store.get_participation(peer_id, name)
    assert row is not None and row.distribution == "kotlin_apk" and row.enabled

  domain_runtime.run(scenario())


def test_selection_change_clears_schema_and_rejects_late_old_callback(
  setup_domain_engine, domain_runtime
):
  peer_id = uuid.uuid4()
  name = f"inkcre/schema-{peer_id.hex}"
  with sqlmodel.Session(setup_domain_engine) as session:
    session.add(PeerModel(id=peer_id, name="schema-cas-test"))
    session.add(ExtensionModel(name=name))
    session.flush()
    session.add(
      ExtensionPeerParticipationModel(
        name=name,
        peer_id=peer_id,
        distribution="python",
        version="1.0.0",
        enabled=False,
        config_schema={"title": "old"},
      )
    )
    session.commit()

  store = ExtensionStateService()

  async def scenario():
    selected = await store.select_participation(peer_id, name, "python", "2.0.0")
    assert selected.config_schema is None
    with setup_domain_engine.connect() as connection:
      assert connection.exec_driver_sql(
        "SELECT config_schema IS NULL "
        "FROM inkcre.extension_peer_participations "
        "WHERE name = %s AND peer_id = %s",
        (name, peer_id),
      ).scalar_one()
    with pytest.raises(ExtensionStateConflictError, match="selection changed"):
      await store.update_participation_config_schema(
        peer_id, name, "python", "1.0.0", {"title": "late old schema"}
      )
    current = await store.get_participation(peer_id, name)
    assert current is not None
    assert (current.distribution, current.version, current.config_schema) == (
      "python",
      "2.0.0",
      None,
    )

  domain_runtime.run(scenario())


def test_setup_source_and_cron_use_simple_core_owned_operations(
  setup_domain_engine, domain_runtime
):
  async def scenario():
    source_type = f"{BookmarkSource.__module__}.{BookmarkSource.__qualname__}"
    await SourceManager.sync_source_types_async({source_type: BookmarkSource})
    await JobManager.sync_job_types()
    source = await SourceManager.create(source_type, nickname="Twitter Bookmarks")
    assert source.id is not None
    form = CronForm(
      schedule="0 6 * * *",
      job_type=SOURCE_COLLECT_JOB_TYPE,
      job_parameters={"source": source.id, "config": {"full": False}},
    )
    cron = await CronManager.create(form)
    assert cron.id is not None
    first = await CronManager.run_now(cron.id)
    second = await CronManager.run_now(cron.id)
    assert second.id != first.id
    rebound = await CronManager.update(cron.id, form.model_copy(update={"enabled": False}))
    assert rebound.enabled is False

  domain_runtime.run(scenario())
