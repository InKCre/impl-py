"""CalDAV and CardDAV access to the shared InKCre info-base."""

from app.business.extension import ExtensionBase
from .config import CalDAVConfig


class Extension(ExtensionBase[CalDAVConfig], ext_id="caldav", config_cls=CalDAVConfig):
  @classmethod
  def api_dependencies(cls):
    return []

  @classmethod
  def _register_apis(cls, router):
    from .connection import register_connection_route
    from .protocol import register_protocol

    register_connection_route(router)
    register_protocol(router)

  @classmethod
  def peer_inbounds(cls):
    from .connection import CONNECTION_INBOUND

    return (CONNECTION_INBOUND,)

  @classmethod
  def _init_resolvers(cls):
    from . import resolver  # noqa: F401
