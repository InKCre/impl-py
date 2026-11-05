"""Runtime projection of optional deployment telemetry and context boundaries."""

import asyncio

from opentelemetry.trace import SpanKind
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from inkcre_core.business.deployment_config import (
  DeploymentConfigManager,
  DeploymentConfigService,
)
from app.settings import settings
from inkcre_core.schemas.observability import CONFIG_KEY, CONFIG_SCHEMA, ObservabilityConfig
from app.version import APPLICATION_VERSION
from inkcre_core.libs.obsrv.main import get_logger
from inkcre_core.libs.obsrv.telemetry import is_enabled, operation, start_telemetry


from inkcre_core.libs.obsrv.propagation import (
  extract_context,
  inject_context as inject_context,
  submission_context as submission_context,
  submission_links as submission_links,
)


DeploymentConfigManager.register_schema(
  CONFIG_SCHEMA, ObservabilityConfig, keys=(CONFIG_KEY,)
)


async def initialize_telemetry() -> None:
  """Read once after database admission; telemetry cannot fail business readiness."""
  if not settings.obsrv.telemetry_enabled:
    return
  attributes: dict[str, str] = {"inkcre.peer.id": str(settings.peer_id)}
  endpoints: dict[str, str] = {}
  try:
    async with asyncio.timeout(2):
      config = await DeploymentConfigService.get(CONFIG_KEY)
    if not isinstance(config, ObservabilityConfig):
      raise ValueError("missing or unexpected observability schema")
    attributes["inkcre.deployment.id"] = str(config.deployment_id)
    if config.otlp_http_endpoints is not None:
      endpoints = config.otlp_http_endpoints.model_dump(mode="json", exclude_none=True)
  except Exception as error:
    # Configuration may contain credentials despite the admitted schema. Never render it.
    get_logger().warning("Telemetry configuration unavailable (%s)", type(error).__name__)
    return
  try:
    start_telemetry(
      enabled=True,
      service_version=APPLICATION_VERSION,
      resource_attributes=attributes,
      endpoints=endpoints,
    )
  except Exception as error:
    get_logger().warning("Telemetry initialization failed (%s)", type(error).__name__)


class TelemetryMiddleware:
  """Observe complete HTTP responses without recording paths, headers or bodies."""

  def __init__(self, app: ASGIApp):
    self.app = app

  async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] != "http" or not is_enabled():
      await self.app(scope, receive, send)
      return
    carrier = {
      key.decode("ascii"): value.decode("latin-1")
      for key, value in scope.get("headers", ())
      if key in {b"traceparent", b"tracestate"}
    }
    method = scope.get("method", "")
    if method not in {
      "GET",
      "POST",
      "PUT",
      "PATCH",
      "DELETE",
      "HEAD",
      "OPTIONS",
      "CONNECT",
      "TRACE",
    }:
      method = "_OTHER"
    with operation(
      "http.server",
      attributes={"http.request.method": method},
      context=extract_context(carrier),
      kind=SpanKind.SERVER,
    ) as observation:
      span = observation.span

      async def observed_send(message: Message) -> None:
        if message["type"] == "http.response.start":
          span.set_attribute("http.response.status_code", message["status"])
          route = scope.get("route")
          if route is not None and isinstance(getattr(route, "path", None), str):
            span.set_attribute("http.route", route.path)
          if message["status"] >= 500:
            observation.outcome = "error"
        await send(message)

      await self.app(scope, receive, observed_send)
