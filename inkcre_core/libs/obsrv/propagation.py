"""Standard trace propagation independent of application initialization."""

from collections.abc import Mapping
from opentelemetry.context import Context
from opentelemetry.trace import Link
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry import trace
from .telemetry import emit_event, is_enabled

PROPAGATOR = TraceContextTextMapPropagator()


def inject_context() -> dict[str, str]:
  """Inject only standard Trace Context when this runtime explicitly enabled telemetry."""
  carrier: dict[str, str] = {}
  if is_enabled():
    PROPAGATOR.inject(carrier)
  return carrier


def submission_context() -> dict[str, str | None]:
  carrier = inject_context()
  state = carrier.get("tracestate")
  if state is not None and len(state.encode("utf-8")) > 512:
    state = None
    emit_event("telemetry.carrier.dropped", {"reason": "tracestate_capacity"})
  return {
    "submission_traceparent": carrier.get("traceparent"),
    "submission_tracestate": state,
  }


def extract_context(carrier: Mapping[str, str]) -> Context:
  """Use the SDK parser within the application's bounded, two-header carrier."""
  if not is_enabled():
    return Context()
  bounded = {
    key: value
    for key in ("traceparent", "tracestate")
    if (value := carrier.get(key)) is not None and len(value.encode("utf-8")) <= 512
  }
  return PROPAGATOR.extract(bounded, context=Context())


def submission_links(parent: str | None, state: str | None) -> tuple[Link, ...]:
  carrier = {}
  if parent is not None:
    carrier["traceparent"] = parent
  if state is not None:
    carrier["tracestate"] = state
  context = trace.get_current_span(extract_context(carrier)).get_span_context()
  return (Link(context),) if context.is_valid else ()
