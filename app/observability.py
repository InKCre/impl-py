"""Runtime projection of optional deployment telemetry and context boundaries."""

import asyncio

import pydantic

from inkcre_core.context import current_core
from inkcre_core.libs.obsrv.backends import LokiBackend, PostgreSQLBackend
from inkcre_core.libs.obsrv.runtime import ObservationRuntime

from opentelemetry.trace import SpanKind
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from inkcre_core.business.deployment_config import (
  DeploymentConfigManager,
  DeploymentConfigService,
)
from app.settings import settings
from inkcre_core.schemas.observability import (
  CONFIG_KEY,
  CONFIG_SCHEMA,
  ObservabilityConfig,
  BACKEND_CONFIG_KEY,
  BACKEND_CONFIG_SCHEMA,
  ObservabilityBackendConfig,
)
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


DeploymentConfigManager.register_schema(
  BACKEND_CONFIG_SCHEMA, ObservabilityBackendConfig, keys=(BACKEND_CONFIG_KEY,)
)


async def initialize_telemetry() -> None:
  """Bind the selected recording/query backend; OTLP is an independent extra exit."""
  core = current_core()
  backend = None
  selected = "postgresql"
  error_message = None
  config = None
  try:
    async with asyncio.timeout(5):
      selection = await DeploymentConfigService.get(BACKEND_CONFIG_KEY)
      if selection is not None and not isinstance(selection, ObservabilityBackendConfig):
        raise ValueError("unexpected observability backend schema")
      selected = selection.backend if selection is not None else "postgresql"
      if selected == "loki" or settings.obsrv.telemetry_enabled:
        config = await DeploymentConfigService.get(CONFIG_KEY)
    if selected == "postgresql":
      backend = PostgreSQLBackend(core.sessions)
    else:
      if not isinstance(config, ObservabilityConfig) or settings.obsrv.loki_url is None:
        raise ValueError("external backend needs deployment identity and private URL")
      url = settings.obsrv.loki_url
      if url.username or url.password or url.query or url.fragment:
        raise ValueError("private Loki URL cannot contain credentials/query/fragment")
      headers = pydantic.TypeAdapter(dict[str, str]).validate_json(
        settings.obsrv.loki_headers.get_secret_value()
      )
      backend = LokiBackend(
        url=str(url), deployment_id=str(config.deployment_id), headers=headers
      )
  except Exception:
    # Missing selection is DB. A failed/invalid selection read is not permission
    # to silently choose DB or redirect writes from an intended external backend.
    error_message = "Observability configuration unavailable or incomplete"
  if core.observability is not None:
    await core.observability.aclose()
  core.observability = ObservationRuntime(
    backend,
    selected=selected,
    peer_id=str(core.options.peer_id),
    error=error_message,
    allow_external_content=settings.obsrv.external_content_enabled,
  )
  core.observability.start()
  if not settings.obsrv.telemetry_enabled:
    return
  if not isinstance(config, ObservabilityConfig):
    get_logger().warning("Telemetry configuration unavailable")
    return
  attributes: dict[str, str] = {
    "inkcre.peer.id": str(settings.peer_id),
    "inkcre.deployment.id": str(config.deployment_id),
  }
  endpoints: dict[str, str] = {}
  if config.otlp_http_endpoints is not None:
    endpoints = config.otlp_http_endpoints.model_dump(mode="json", exclude_none=True)

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
