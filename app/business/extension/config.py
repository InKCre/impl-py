"""Registry-origin authority for Core Extension Host operations."""

from urllib.parse import urlsplit, urlunsplit
import typing

import pydantic

from inkcre_core.business.deployment_config import (
  DeploymentConfigManager,
  DeploymentConfigService,
)
from inkcre_core.business.peer import PeerManager
from app.settings import settings


EXTENSION_REGISTRY_CONFIG_KEY = "extension.registry"
EXTENSION_REGISTRY_CONFIG_SCHEMA = "extension.registry.config.v1"


def normalize_registry_origin(value: str) -> str:
  parts = urlsplit(value.strip())
  if (
    parts.scheme not in {"http", "https"}
    or not parts.netloc
    or parts.username is not None
    or parts.password is not None
    or parts.path not in {"", "/"}
    or parts.query
    or parts.fragment
  ):
    raise ValueError("Extension Registry URL must be one HTTP(S) origin")
  return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


class ExtensionRegistryDeploymentConfig(pydantic.BaseModel):
  """Deployment default overridden only by an executing Host Peer."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  extension_registry_url: str | None = None

  @pydantic.field_validator("extension_registry_url")
  @classmethod
  def validate_registry_origin(cls, value: str | None) -> str | None:
    if value is None or not value.strip():
      return None
    return normalize_registry_origin(value)


DeploymentConfigManager.register_schema(
  EXTENSION_REGISTRY_CONFIG_SCHEMA,
  ExtensionRegistryDeploymentConfig,
  keys=(EXTENSION_REGISTRY_CONFIG_KEY,),
)


async def resolve_extension_registry_origin() -> str:
  """Resolve one immutable origin snapshot for a Host operation."""
  peer_override = (await PeerManager.get_current_config_async()).extension_registry_url
  if peer_override is not None:
    return peer_override
  deployment = await DeploymentConfigService.get(EXTENSION_REGISTRY_CONFIG_KEY)
  if deployment is not None:
    configured = typing.cast(ExtensionRegistryDeploymentConfig, deployment)
    if configured.extension_registry_url is not None:
      return configured.extension_registry_url
  return normalize_registry_origin(settings.extension_registry_url)
