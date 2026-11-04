"""Test-only composition helper for Extension-owned HTTP surfaces."""

import asyncio
from dataclasses import dataclass
import os
import typing

import fastapi
from fastapi.testclient import TestClient

from app.business.extension import ExtensionBase


@dataclass
class _TestActiveModel:
  name: str
  config: dict[str, typing.Any]
  state: dict[str, typing.Any]

  async def update_config_async(self, value):
    self.config = dict(value)
    return self

  async def update_config_schema_async(self, _schema):
    return self

  async def read_state_async(self):
    return dict(self.state)

  async def mutate_state_async(self, transform):
    self.state = transform(dict(self.state))
    return dict(self.state)

  async def mutate_config_and_state_async(self, transform):
    self.config, self.state = transform(dict(self.config), dict(self.state))
    return dict(self.config), dict(self.state)


@dataclass
class PublishedExtension:
  app: fastapi.FastAPI
  extension: type[ExtensionBase]
  client: TestClient

  def unpublish(self) -> None:
    self.extension.unpublish()
    self.extension.unbind()
    self.extension.release_runtime()


def publish_extension(
  extension: type[ExtensionBase],
  config: dict[str, typing.Any] | None = None,
  *,
  app: fastapi.FastAPI | None = None,
  raise_server_exceptions: bool = True,
  persist_config: bool = False,
) -> PublishedExtension:
  from app.core import CORE, CoreMiddleware

  runtime_app = app or fastapi.FastAPI()
  runtime_app.add_middleware(CoreMiddleware, core=CORE)
  runtime_config = dict(config or {})
  runtime_state: dict[str, typing.Any] = {}

  if persist_config:
    if not os.getenv("INKCRE_TEST_DATABASE_URL"):
      raise RuntimeError("Persisted Extension setup requires an explicit test database")
    from inkcre_core.schemas.extension import ExtensionModel
    from tests.database import TestSession

    with TestSession() as session:
      name = f"inkcre/{extension.__extid__}"
      installed = session.get(ExtensionModel, name)
      if installed is None:
        installed = ExtensionModel(name=name)
      installed.config = runtime_config
      session.add(installed)
      session.commit()

  extension.unpublish()
  extension.unbind()
  extension.release_runtime()
  extension.bind(
    _TestActiveModel(
      name=f"inkcre/{extension.__extid__}",
      config=runtime_config,
      state=runtime_state,
    )
  )

  async def start():
    from app.engine import ASYNC_DB_ENGINE

    try:
      await extension.on_start_async(runtime_app)
    finally:
      await ASYNC_DB_ENGINE.dispose()

  asyncio.run(start())
  return PublishedExtension(
    runtime_app,
    extension,
    TestClient(runtime_app, raise_server_exceptions=raise_server_exceptions),
  )
