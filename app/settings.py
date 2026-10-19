"""Application settings module using pydantic-settings.

This module provides centralized configuration management with validation,
defaults, and type safety for environment variables.
"""

import os
import uuid
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.database_contract.constants import JWT_MINIMUM_SECRET_BYTES
from libs.obsrv.setting import ObsrvSetting


class Settings(BaseSettings):
  """Application settings loaded from environment variables.

  All settings have type validation and optional defaults.
  Pydantic-settings automatically loads values from environment variables.
  """

  model_config = SettingsConfigDict(
    env_file=os.getenv("INKCRE_ENV_FILE", ".env") or None,
    env_file_encoding="utf-8",
    case_sensitive=False,
    extra="ignore",  # Ignore extra environment variables
    hide_input_in_errors=True,
    env_nested_delimiter="__",
  )

  # Server settings
  host: str = Field(default="0.0.0.0", description="Server host address")
  port: int = Field(default=8000, description="Server port")

  # Database settings
  database_url: str = Field(
    ...,  # Required field
    description="PostgreSQL database connection URL",
  )
  database_scale_0: bool = Field(
    default=False,
    description=(
      "Set true when the database scales to zero; enables pool_pre_ping and "
      "disables LISTEN/NOTIFY assumptions"
    ),
  )

  # JWT authentication
  jwt_secret: str = Field(
    ...,  # Required field
    min_length=JWT_MINIMUM_SECRET_BYTES,
    description="Secret key for JWT token signing and verification",
  )
  jwt_default_lifetime_seconds: int = Field(
    default=60,
    gt=0,
    description="Default Peer JWT signing lifetime; not an admission limit",
  )
  twitter_x_monkey_jwt_lifetime_seconds: int = Field(
    default=7 * 24 * 60 * 60,
    gt=0,
    description="Signing lifetime for the Twitter x-monkey session and its renewal",
  )

  # Observability settings
  obsrv: ObsrvSetting = Field(default_factory=ObsrvSetting)

  # Peer settings
  peer_id: uuid.UUID = Field(
    default_factory=uuid.uuid4,
    description="Unique identifier for this Peer instance (UUID v4)",
  )
  peer_name: str = Field(
    default="core-py",
    description="Human-readable name for this Peer instance",
  )
  peer_lease_ttl_seconds: int = Field(default=90, gt=0)
  peer_lease_renew_interval_seconds: int = Field(default=30, gt=0)
  peer_http_timeout_seconds: float = Field(default=30, gt=0)

  # Peer-local semantic retrieval maintenance settings
  semantic_retrieval_maintenance_interval_seconds: int = Field(default=60, gt=0)
  semantic_retrieval_maintenance_max_embeddings: int = Field(default=100, gt=0)
  semantic_retrieval_maintenance_batch_size: int = Field(default=20, gt=0)
  semantic_retrieval_maintenance_scan_page_size: int = Field(default=100, gt=0)

  # Public Extension Registry and native Python Distribution consumer
  extension_registry_url: str = Field(
    default="https://registry.inkcre.dev",
    description="Public Extension Registry origin used for install and new enable",
  )
  extension_registry_timeout_seconds: float = Field(
    default=5.0,
    gt=0,
    le=30,
    description="Bounded timeout for public Extension Registry requests",
  )

  @field_validator("database_url")
  @classmethod
  def use_psycopg_driver(cls, v: str) -> str:
    """Normalize generic and legacy PostgreSQL URLs to the installed driver."""
    if v.startswith("postgres://"):
      return v.replace("postgres://", "postgresql+psycopg://", 1)
    if v.startswith("postgresql+psycopg2://"):
      return v.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if v.startswith("postgresql://"):
      return v.replace("postgresql://", "postgresql+psycopg://", 1)
    return v


def get_settings() -> Settings:
  """Get or create the global settings instance.

  This function can be used as a dependency in FastAPI endpoints.

  Returns:
      Settings instance
  """
  return Settings()  # type: ignore


# Global settings instance
# This should be imported and used throughout the application
settings = get_settings()
