import typing
from typing import Optional as Opt

from fastapi import APIRouter
import sqlmodel

from app.business.extension.main import ExtensionBase, PublicHTTPRoute
from app.business.info_base.resolver import ResolverManager
from app.business.source import SourceManager


class TwitterExtensionConfig(sqlmodel.SQLModel):
  # TODO move to SourceConfig
  backend: typing.Literal["official", "twikit"] = "official"
  api_language: str = "en-US"
  proxy: Opt[str] = None
  client_id: str = ""
  client_secret: str = ""
  email: str = ""
  username: str = ""
  password: str = ""
  totp_secret: Opt[str] = None


from .setup_flow import TwitterExtensionState


class Extension(
  ExtensionBase[TwitterExtensionConfig],
  ext_id="twitter",
  config_cls=TwitterExtensionConfig,
  state_cls=TwitterExtensionState,
):
  @classmethod
  def _init_resolvers(cls):
    from .resolver import (
      ArchiveMediaResolver,
      ArchivePostResolver,
      ArchiveUserResolver,
      TweetResolver,
    )

    ResolverManager.register_resolver(TweetResolver)
    ResolverManager.register_resolver(ArchivePostResolver)
    ResolverManager.register_resolver(ArchiveUserResolver)
    ResolverManager.register_resolver(ArchiveMediaResolver)

  @classmethod
  def _init_sources(cls):
    from .bookmark import Source as BookmarkSource

    SourceManager.add_source_type(BookmarkSource)

  @classmethod
  async def on_close(cls):
    from .api import TwitterAPI

    failures: list[Exception] = []
    try:
      await TwitterAPI.close_singleton()
    except Exception as error:
      failures.append(error)
    try:
      await super().on_close()
    except Exception as error:
      failures.append(error)
    if len(failures) == 1:
      raise failures[0]
    if failures:
      raise ExceptionGroup("Twitter Extension close failed", failures)

  @classmethod
  async def on_start_async(cls, app):
    from .setup_flow import _reconcile_oauth_state

    await _reconcile_oauth_state()
    await super().on_start_async(app)

  @classmethod
  def _register_apis(cls, router: APIRouter):
    from .setup_flow import register_setup_routes
    from .archive_http import router as archive_http_router

    register_setup_routes(router)
    router.include_router(archive_http_router, prefix="/x-monkey")

  @classmethod
  def api_dependencies(cls) -> list[typing.Any]:
    """Compose Peer auth only around setup; OAuth callback stays public."""
    return []

  @classmethod
  def peer_inbounds(cls) -> tuple[typing.Any, ...]:
    from .setup_flow import TWITTER_SETUP_INBOUNDS

    return TWITTER_SETUP_INBOUNDS

  @classmethod
  def public_http_routes(cls) -> tuple[PublicHTTPRoute, ...]:
    return (
      PublicHTTPRoute(method="GET", path="/auth/callback"),
      PublicHTTPRoute(method="POST", path="/x-monkey/session"),
      PublicHTTPRoute(method="GET", path="/x-monkey/account"),
      PublicHTTPRoute(method="POST", path="/x-monkey/disconnect"),
      PublicHTTPRoute(method="GET", path="/x-monkey/api/v1/session"),
      PublicHTTPRoute(method="POST", path="/x-monkey/api/v1/archive/query"),
      PublicHTTPRoute(method="POST", path="/x-monkey/api/v1/archive/submit"),
    )

  @classmethod
  async def update_config_async(
    cls,
    value: dict[str, typing.Any] | TwitterExtensionConfig,
  ) -> TwitterExtensionConfig:
    """Keep generic config writes consistent with setup-owned OAuth state."""
    from .setup_flow import _fingerprint, _invalidate_mismatched_oauth_state

    validated = (
      TwitterExtensionConfig.model_validate(value) if isinstance(value, dict) else value
    )

    def update(config_model, state_model):
      config = typing.cast(TwitterExtensionConfig, config_model)
      state = typing.cast(TwitterExtensionState, state_model)
      if _fingerprint(validated) != _fingerprint(config):
        state, _ = _invalidate_mismatched_oauth_state(
          validated,
          state,
          reason="OAuth App changed",
        )
      return validated, state

    config, _ = await cls.mutate_config_and_state_async(update)
    return config
