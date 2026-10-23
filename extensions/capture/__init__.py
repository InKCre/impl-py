"""Optional Capture graph interpretation for a Python Peer."""

import pydantic

from app.business.extension import ExtensionBase


class CaptureConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")


class Extension(ExtensionBase[CaptureConfig], ext_id="capture", config_cls=CaptureConfig):
  @classmethod
  def _init_resolvers(cls) -> None:
    from . import resolver as resolver_module  # noqa: F401
