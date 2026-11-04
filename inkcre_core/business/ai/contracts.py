"""AI dialect adapter boundary and stable execution failures."""

import abc
from collections.abc import Sequence
from dataclasses import dataclass

import pydantic

from inkcre_core.schemas.ai import (
  AICapabilityType,
  AssistantMessage,
  FunctionTool,
  Message,
  ToolChoice,
)
from inkcre_core.schemas.info_base.main import Vector


@dataclass(frozen=True)
class AIExecutionRequirement:
  """Static capability shape used for peer-local execution eligibility."""

  capability: AICapabilityType
  input_modalities: frozenset[str]
  output_modalities: frozenset[str]
  features: frozenset[str] = frozenset()
  tool_choice: ToolChoice | None = None


class DuplicateAIDialectRegistrationError(ValueError):
  """Two adapter implementations claimed the same exact dialect ID."""


class UnknownAIDialectError(LookupError):
  """The current peer has no adapter for an exact persisted dialect ID."""


class AIProviderNotFoundError(LookupError):
  """A requested AI model references no provider visible to this peer."""


class AIModelNotFoundError(LookupError):
  """A requested AI model does not exist."""


class AIProviderDisabledError(RuntimeError):
  """A disabled provider rejected a new operation."""


class AIModelDisabledError(RuntimeError):
  """A disabled model rejected a new operation."""


class InvalidAIProviderConfigError(RuntimeError):
  """Persisted provider config does not satisfy its dialect contract."""


class AICapabilityUnavailableError(RuntimeError):
  """The selected provider-bound model does not declare an operation."""


class AIFeatureUnavailableError(RuntimeError):
  """Effective model/adapter support lacks a requested feature."""


class AIInputUnavailableError(RuntimeError):
  """A canonical input cannot be conveyed through the selected dialect."""


class AIOutputContractError(RuntimeError):
  """A provider response violated the canonical capability result contract."""


class AIDialectAdapter(abc.ABC):
  """Graph-blind provider config/client construction and wire translation."""

  supported_features: dict[AICapabilityType, frozenset[str]] = {}
  supported_input_modalities: dict[AICapabilityType, frozenset[str]] = {}

  @abc.abstractmethod
  async def embed(
    self,
    config: pydantic.BaseModel,
    native_model_id: str,
    inputs: Sequence[str],
    dimensions: int,
  ) -> tuple[Vector, ...]: ...

  @abc.abstractmethod
  async def chat(
    self,
    config: pydantic.BaseModel,
    native_model_id: str,
    messages: Sequence[Message],
    tools: Sequence[FunctionTool],
    tool_choice: ToolChoice | None,
  ) -> AssistantMessage: ...

  def supports_feature(self, capability: AICapabilityType, feature: str) -> bool:
    return feature in self.supported_features.get(capability, frozenset())

  def supports_input_modality(
    self,
    capability: AICapabilityType,
    modality: str,
  ) -> bool:
    return modality in self.supported_input_modalities.get(capability, frozenset())

  def supports_tool_choice(self, tool_choice: ToolChoice) -> bool:
    del tool_choice
    return False
