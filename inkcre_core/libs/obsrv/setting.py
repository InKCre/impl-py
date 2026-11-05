"""Observability settings."""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class ObsrvSetting(BaseSettings):
  """Observability settings."""

  telemetry_enabled: bool = Field(
    default=False,
    description="Enable optional metadata-only OpenTelemetry export for this Peer.",
  )

  agent_debug: bool = Field(
    default=False,
    description="Record Agent inputs, tool contracts and execution events for development.",
  )

  logging_backend: Optional[str] = Field(
    default="postgresql",
    description="Logging backend to use (e.g., 'logtail', 'postgresql')",
  )
  logging_backend_level: int = Field(
    default=20,
    description="Minimum log level for sending logs to the backend",
  )
  logtail_source_token: Optional[str] = Field(
    default=None, description="Better Stack (Logtail) source token for remote logging"
  )
  logtail_host: Optional[str] = Field(
    default=None, description="Better Stack (Logtail) host URL"
  )
