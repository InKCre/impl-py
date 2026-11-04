"""Provider-neutral AI model capability declarations."""

import typing

import pydantic


AICapabilityType: typing.TypeAlias = typing.Literal["embedding", "chat"]
AIModality: typing.TypeAlias = str
AIFeature: typing.TypeAlias = str


def _canonical_string_set(value: typing.Any) -> tuple[str, ...]:
  if not isinstance(value, (list, tuple, set, frozenset)):
    raise ValueError("value must be an array of strings")
  items = tuple(value)
  if not all(isinstance(item, str) and item for item in items):
    raise ValueError("values must be non-empty strings")
  if len(items) != len(set(items)):
    raise ValueError("duplicate values are not allowed")
  return tuple(sorted(items))


class AICapability(pydantic.BaseModel):
  """Fields shared by every typed capability declaration."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: AICapabilityType
  input_modalities: tuple[AIModality, ...]
  output_modalities: tuple[AIModality, ...]
  features: tuple[AIFeature, ...] = ()

  _normalize_input_modalities = pydantic.field_validator("input_modalities", mode="before")(
    _canonical_string_set
  )
  _normalize_output_modalities = pydantic.field_validator(
    "output_modalities", mode="before"
  )(_canonical_string_set)
  _normalize_features = pydantic.field_validator("features", mode="before")(
    _canonical_string_set
  )


class EmbeddingCapability(AICapability):
  type: typing.Literal["embedding"] = "embedding"


class ChatCapability(AICapability):
  type: typing.Literal["chat"] = "chat"


AIModelCapability: typing.TypeAlias = typing.Annotated[
  EmbeddingCapability | ChatCapability,
  pydantic.Field(discriminator="type"),
]

CAPABILITIES_ADAPTER = pydantic.TypeAdapter(tuple[AIModelCapability, ...])


def normalize_capabilities(value: typing.Any) -> tuple[AIModelCapability, ...]:
  """Validate and canonically order one declaration per capability type."""
  capabilities = CAPABILITIES_ADAPTER.validate_python(value)
  types = tuple(capability.type for capability in capabilities)
  if len(types) != len(set(types)):
    raise ValueError("duplicate AI capability types are not allowed")
  return tuple(sorted(capabilities, key=lambda capability: capability.type))
