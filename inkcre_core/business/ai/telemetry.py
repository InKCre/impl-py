"""Content-free mapping for the pinned GenAI semantic conventions.

Keys follow semantic-conventions-genai e07f4ebacb08f56db8c4c882d117720333fbca04.
Provider responses are untrusted: copy numeric usage and known finish reasons only.
"""

from collections.abc import Sequence
import re

from openai.types.completion_usage import CompletionUsage
from openai.types.create_embedding_response import Usage as EmbeddingUsage
from opentelemetry import trace
from opentelemetry.util.types import AttributeValue

from inkcre_core.libs.obsrv.telemetry import is_enabled, record_ai_usage


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_FINISH_REASONS = frozenset(
  {"stop", "length", "tool_calls", "content_filter", "function_call"}
)


def model_attributes(
  native_model_id: str, dialect: str, provider_id: int
) -> dict[str, AttributeValue]:
  attributes: dict[str, AttributeValue] = {
    "inkcre.ai.provider.id": provider_id,
    "inkcre.ai.dialect": dialect if _IDENTIFIER.fullmatch(dialect) else "other",
    "gen_ai.provider.name": {
      "core.openai-compatible.v1": "openai",
      "core.alibaba-model-studio.v1": "alibaba",
    }.get(dialect, "other"),
  }
  # This is the configured model identifier, never the provider's arbitrary echo.
  if _IDENTIFIER.fullmatch(native_model_id):
    attributes["gen_ai.request.model"] = native_model_id
  return attributes


def record_response_usage(
  operation: str,
  usage: CompletionUsage | EmbeddingUsage | None,
  finish_reasons: Sequence[str] = (),
) -> None:
  """Record one response's totals once; absent or invalid totals stay unknown."""
  if not is_enabled():
    return
  span = trace.get_current_span()
  input_tokens = getattr(usage, "prompt_tokens", None)
  output_tokens = getattr(usage, "completion_tokens", None)
  input_tokens = (
    input_tokens if type(input_tokens) is int and 0 <= input_tokens < 2**63 else None
  )
  output_tokens = (
    output_tokens if type(output_tokens) is int and 0 <= output_tokens < 2**63 else None
  )
  for direction, value in (("input", input_tokens), ("output", output_tokens)):
    span.set_attribute(
      f"inkcre.ai.usage.{direction}.source",
      "provider" if value is not None else "unavailable",
    )
    if value is not None:
      span.set_attribute(f"gen_ai.usage.{direction}_tokens", value)
  if isinstance(usage, CompletionUsage):
    for key, value in (
      (
        "gen_ai.usage.cache_read.input_tokens",
        getattr(usage.prompt_tokens_details, "cached_tokens", None),
      ),
      (
        "gen_ai.usage.reasoning.output_tokens",
        getattr(usage.completion_tokens_details, "reasoning_tokens", None),
      ),
    ):
      if type(value) is int and 0 <= value < 2**63:
        span.set_attribute(key, value)
  if finish_reasons:
    span.set_attribute(
      "gen_ai.response.finish_reasons",
      tuple(
        reason if isinstance(reason, str) and reason in _FINISH_REASONS else "other"
        for reason in finish_reasons[:16]
      ),
    )
  record_ai_usage(operation, input_tokens=input_tokens, output_tokens=output_tokens)
