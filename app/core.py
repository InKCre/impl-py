"""Application-owned composition of the embeddable SDK."""

from starlette.types import ASGIApp, Receive, Scope, Send

from inkcre_core import Core, CoreOptions
from app.settings import settings
from app.version import APPLICATION_VERSION


def create_core() -> Core:
  """Compose one service-owned SDK instance from application settings."""
  return Core(
    settings.database_url,
    pool_pre_ping=settings.database_scale_0,
    options=CoreOptions(
      peer_id=settings.peer_id,
      peer_name=settings.peer_name,
      application_version=APPLICATION_VERSION,
      jwt_secret=settings.jwt_secret,
      jwt_default_lifetime_seconds=settings.jwt_default_lifetime_seconds,
      peer_http_timeout_seconds=settings.peer_http_timeout_seconds,
      agent_debug=settings.obsrv.agent_debug,
    ),
  )


CORE = create_core()


class CoreMiddleware:
  """Bind the application's SDK instance for requests, lifespan and child work."""

  def __init__(self, app: ASGIApp, core: Core):
    self.app = app
    self.core = core

  async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
    with self.core.bind():
      await self.app(scope, receive, send)
