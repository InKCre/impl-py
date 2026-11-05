"""Non-secret deployment telemetry configuration shared by admitted Peers."""

import uuid
import pydantic

CONFIG_KEY = "inkcre.observability"
CONFIG_SCHEMA = "inkcre.observability.v1"


def _credential_free_url(value: pydantic.AnyHttpUrl | None):
  if value is not None and (
    value.username or value.password or value.query or value.fragment
  ):
    raise ValueError("observability URLs cannot contain credentials, queries or fragments")
  return value


class TelemetryEndpoints(pydantic.BaseModel):
  """Complete OTLP/HTTP destinations; authentication stays in local runtime config."""

  model_config = pydantic.ConfigDict(extra="forbid")
  traces: pydantic.AnyHttpUrl | None = None
  logs: pydantic.AnyHttpUrl | None = None
  metrics: pydantic.AnyHttpUrl | None = None

  _urls = pydantic.field_validator("traces", "logs", "metrics")(_credential_free_url)


class ObservabilityConfig(pydantic.BaseModel):
  """Stable deployment identity and optional metadata-only diagnostic destinations."""

  model_config = pydantic.ConfigDict(extra="forbid")
  deployment_id: uuid.UUID
  otlp_http_endpoints: TelemetryEndpoints | None = None
  diagnostics_url: pydantic.AnyHttpUrl | None = None

  _url = pydantic.field_validator("diagnostics_url")(_credential_free_url)
