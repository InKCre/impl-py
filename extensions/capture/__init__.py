"""Capture contract execution and interpretation for an admitted Python Peer."""

import fastapi
import pydantic

from app.business.extension import ExtensionBase
from app.business.peer import PeerHTTPInbound

from .schema import CAPTURE_CAPABILITY


class CaptureConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")


class Extension(ExtensionBase[CaptureConfig], ext_id="capture", config_cls=CaptureConfig):
  @classmethod
  def _init_resolvers(cls) -> None:
    from . import resolver as resolver_module  # noqa: F401

  @classmethod
  def peer_inbounds(cls):
    return (PeerHTTPInbound(CAPTURE_CAPABILITY, "POST", "/capture/submit"),)

  @classmethod
  def _register_apis(cls, router: fastapi.APIRouter) -> None:
    from .api import router as capture_router

    router.include_router(capture_router)
