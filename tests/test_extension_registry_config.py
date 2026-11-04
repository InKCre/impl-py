from unittest.mock import AsyncMock

import pytest

from inkcre_core.business.deployment_config import DeploymentConfigService
from app.business.extension.config import (
  ExtensionRegistryDeploymentConfig,
  normalize_registry_origin,
  resolve_extension_registry_origin,
)
from inkcre_core.business.peer import PeerManager
from inkcre_core.schemas.peer import CorePeerConfig
from app.settings import settings


@pytest.mark.parametrize(
  "value",
  [
    "ftp://registry.test",
    "https://user@registry.test",
    "https://registry.test/simple/",
    "https://registry.test?channel=preview",
    "https://registry.test#fragment",
  ],
)
def test_registry_origin_rejects_non_origin_values(value: str):
  with pytest.raises(ValueError, match="one HTTP\\(S\\) origin"):
    normalize_registry_origin(value)


def test_registry_origin_normalizes_one_trailing_slash():
  assert normalize_registry_origin(" https://registry.test/ ") == "https://registry.test"


def test_executing_peer_registry_override_wins(monkeypatch, async_runner):
  monkeypatch.setattr(
    PeerManager,
    "get_current_config_async",
    AsyncMock(
      return_value=CorePeerConfig(extension_registry_url="https://peer.registry.test")
    ),
  )
  monkeypatch.setattr(
    DeploymentConfigService,
    "get",
    AsyncMock(side_effect=AssertionError("deployment config must not be read")),
  )

  assert (
    async_runner.run(resolve_extension_registry_origin()) == "https://peer.registry.test"
  )


def test_deployment_registry_default_precedes_process_fallback(monkeypatch, async_runner):
  monkeypatch.setattr(
    PeerManager,
    "get_current_config_async",
    AsyncMock(return_value=CorePeerConfig()),
  )
  monkeypatch.setattr(
    DeploymentConfigService,
    "get",
    AsyncMock(
      return_value=ExtensionRegistryDeploymentConfig(
        extension_registry_url="https://deployment.registry.test/"
      )
    ),
  )

  assert (
    async_runner.run(resolve_extension_registry_origin())
    == "https://deployment.registry.test"
  )


def test_process_registry_origin_is_the_final_fallback(monkeypatch, async_runner):
  monkeypatch.setattr(
    PeerManager,
    "get_current_config_async",
    AsyncMock(return_value=CorePeerConfig()),
  )
  monkeypatch.setattr(DeploymentConfigService, "get", AsyncMock(return_value=None))

  assert async_runner.run(resolve_extension_registry_origin()) == normalize_registry_origin(
    settings.extension_registry_url
  )


def test_deployment_registry_contract_is_strict():
  assert (
    ExtensionRegistryDeploymentConfig.model_validate(
      {"extension_registry_url": "https://registry.test/"}
    ).extension_registry_url
    == "https://registry.test"
  )
  with pytest.raises(ValueError):
    ExtensionRegistryDeploymentConfig.model_validate(
      {
        "extension_registry_url": "https://registry.test",
        "unexpected": True,
      }
    )
