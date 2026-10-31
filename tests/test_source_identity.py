"""Source code can move without changing persisted execution identity."""

import pydantic
import pytest

from app.business.source import SourceBase, SourceManager
from app.business.source.job import SourceCollectJobHandler
from app.schemas.source import SourceModel, SourceCollectParameters
from app.schemas.job import JobModel


class EmptyConfig(pydantic.BaseModel):
  pass


def test_explicit_identity_survives_class_move_and_executes_existing_source(
  async_runner, monkeypatch
):
  monkeypatch.setattr(SourceManager, "_SOURCE_CLASSES", {})
  monkeypatch.setattr(SourceManager, "SOURCES", {})
  received = []
  existing_type = "extensions.example.old_module.Source"

  class RenamedSource(
    SourceBase[EmptyConfig],
    config_cls=EmptyConfig,
  ):
    source_type = existing_type

    async def collect(self, job, config):
      received.append(self._id)

  RenamedSource.__module__ = "extensions.example.new_module"

  async def get(source_id):
    return SourceModel(id=source_id, type=existing_type, config={})

  monkeypatch.setattr(SourceManager, "get", get)
  SourceManager.add_source_type(RenamedSource)
  async_runner.run(
    SourceCollectJobHandler.handle(
      JobModel(type=SourceCollectJobHandler.type, parameters={}),
      SourceCollectParameters(source=17),
    )
  )
  assert received == [17]
  assert tuple(SourceManager._SOURCE_CLASSES) == (existing_type,)

  with pytest.raises(ValueError, match="already registered"):

    class ConflictingSource(
      SourceBase[EmptyConfig],
      config_cls=EmptyConfig,
    ):
      source_type = existing_type

      async def collect(self, job, config):
        pass

  assert SourceManager._SOURCE_CLASSES[existing_type] is RenamedSource

  class ChildWithoutIdentity(RenamedSource, config_cls=EmptyConfig):
    pass

  child_type = ChildWithoutIdentity.__module__ + "." + ChildWithoutIdentity.__qualname__
  assert SourceManager._SOURCE_CLASSES[child_type] is ChildWithoutIdentity
  assert ChildWithoutIdentity.source_type != existing_type


def test_legacy_wheel_omitting_identity_keeps_its_original_registration(monkeypatch):
  monkeypatch.setattr(SourceManager, "_SOURCE_CLASSES", {})

  class LegacySource(SourceBase[EmptyConfig], config_cls=EmptyConfig):
    async def collect(self, job, config):
      pass

  expected = LegacySource.__module__ + "." + LegacySource.__qualname__
  assert SourceManager._SOURCE_CLASSES == {expected: LegacySource}
