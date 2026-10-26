"""Optional local-file graph interpretation for a Python Peer."""

import pydantic

from app.business.extension import ExtensionBase


class LocalFilesConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")


class Extension(
  ExtensionBase[LocalFilesConfig], ext_id="local-files", config_cls=LocalFilesConfig
):
  @classmethod
  def _init_resolvers(cls) -> None:
    from . import resolver as resolver_module  # noqa: F401
