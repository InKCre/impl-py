"""Shared AI fact management and peer-local capability routing."""

from collections.abc import Sequence
from dataclasses import dataclass
import math
import typing

import pydantic
from opentelemetry.trace import SpanKind

from inkcre_core.database_contract.profile import BUILTIN_AI_DIALECTS_BY_ID
from inkcre_core.schemas.ai import (
  AICapabilityType,
  AIDialectID,
  AIModelCapability,
  AIModelID,
  AIModelModel,
  AIProviderModel,
  AssistantMessage,
  FunctionTool,
  ImageContentPart,
  Message,
  AudioContentPart,
  TextContentPart,
  ToolChoice,
  UserMessage,
  VideoContentPart,
  validate_message_history,
)
from inkcre_core.schemas.info_base.main import Vector

from inkcre_core.persistence.ai.uow import ai_uow
from inkcre_core.libs.obsrv.telemetry import operation

from .telemetry import model_attributes

from .contracts import (
  AICapabilityUnavailableError,
  AIDialectAdapter,
  AIExecutionRequirement,
  AIFeatureUnavailableError,
  AIInputUnavailableError,
  AIModelDisabledError,
  AIModelNotFoundError,
  AIOutputContractError,
  AIProviderDisabledError,
  AIProviderNotFoundError,
  DuplicateAIDialectRegistrationError,
  InvalidAIProviderConfigError,
  UnknownAIDialectError,
)


ConfigModelT = typing.TypeVar("ConfigModelT", bound=pydantic.BaseModel)
AdapterT = typing.TypeVar("AdapterT", bound=type[AIDialectAdapter])


@dataclass(frozen=True)
class _DialectRegistration:
  adapter: AIDialectAdapter
  adapter_type: type[AIDialectAdapter]
  config_model: type[pydantic.BaseModel]
  description: str


@dataclass(frozen=True)
class _ExecutionTarget:
  model: AIModelModel
  provider: AIProviderModel
  adapter: AIDialectAdapter
  config: pydantic.BaseModel


class AIManager:
  """Sole domain manager for AI dialects, shared facts and execution."""

  _DIALECTS: dict[AIDialectID, _DialectRegistration] = {}

  @classmethod
  async def get_model(cls, model_id: AIModelID) -> AIModelModel | None:
    """Read a model without requiring its provider/dialect to execute here."""
    async with ai_uow() as models:
      return await models.get_model(model_id)

  @classmethod
  async def list_models(
    cls, *, limit: int | None = None, cursor: int | None = None
  ) -> tuple[list[AIModelModel], int | None]:
    async with ai_uow() as models:
      return await models.list_models(limit=limit, cursor=cursor)

  @classmethod
  def register_dialect(
    cls,
    dialect_id: AIDialectID,
    *,
    description: str,
    config_model: type[ConfigModelT],
  ) -> typing.Callable[[AdapterT], AdapterT]:
    """Bind one adapter class to an exact ID and return it unchanged."""

    def decorator(adapter_type: AdapterT) -> AdapterT:
      existing = cls._DIALECTS.get(dialect_id)
      if existing is not None:
        if (
          existing.adapter_type is adapter_type
          and existing.config_model is config_model
          and existing.description == description
        ):
          return adapter_type
        raise DuplicateAIDialectRegistrationError(
          f"AI dialect {dialect_id!r} is already registered by "
          f"{existing.adapter_type.__qualname__}"
        )
      cls._DIALECTS[dialect_id] = _DialectRegistration(
        adapter=adapter_type(),
        adapter_type=adapter_type,
        config_model=config_model,
        description=description,
      )
      return adapter_type

    return decorator

  @classmethod
  async def sync_dialects_async(cls) -> None:
    records = []
    for dialect_id, registration in cls._DIALECTS.items():
      builtin = BUILTIN_AI_DIALECTS_BY_ID.get(dialect_id)
      records.append(
        {
          "id": dialect_id,
          "description": builtin.description
          if builtin is not None
          else registration.description,
          "config_schema": builtin.config_schema
          if builtin is not None
          else registration.config_model.model_json_schema(),
        }
      )
    async with ai_uow() as models:
      await models.sync_dialects(records)

  @classmethod
  def _registration(cls, dialect_id: AIDialectID) -> _DialectRegistration:
    try:
      return cls._DIALECTS[dialect_id]
    except KeyError as error:
      raise UnknownAIDialectError(
        f"No local adapter implements AI dialect {dialect_id!r}"
      ) from error

  @classmethod
  async def _load_target_async(cls, model_id: AIModelID) -> _ExecutionTarget:
    async with ai_uow() as models:
      model = await models.get_model(model_id)
      provider = None if model is None else await models.get_provider(model.provider)
    return cls._execution_target(model_id, model, provider)

  @classmethod
  def _execution_target(
    cls, model_id: AIModelID, model: AIModelModel | None, provider: AIProviderModel | None
  ) -> _ExecutionTarget:
    if model is None:
      raise AIModelNotFoundError(f"AI model {model_id} does not exist")
    if not model.enabled:
      raise AIModelDisabledError(f"AI model {model_id} is disabled")
    if provider is None:
      raise AIProviderNotFoundError(
        f"AI model {model_id} references missing provider {model.provider}"
      )
    if not provider.enabled:
      raise AIProviderDisabledError(f"AI provider {provider.id} is disabled")
    registration = cls._registration(provider.dialect)
    try:
      config = registration.config_model.model_validate(provider.config)
    except pydantic.ValidationError as error:
      raise InvalidAIProviderConfigError(
        f"AI provider {provider.id} does not satisfy dialect {provider.dialect!r}"
      ) from error
    return _ExecutionTarget(model, provider, registration.adapter, config)

  @staticmethod
  def _capability(
    model: AIModelModel,
    capability_type: AICapabilityType,
  ) -> AIModelCapability:
    capability = next(
      (item for item in model.capabilities if item.type == capability_type),
      None,
    )
    if capability is None:
      raise AICapabilityUnavailableError(
        f"AI model {model.id} does not declare {capability_type!r}"
      )
    return capability

  @staticmethod
  def _require_modalities(
    model: AIModelModel,
    capability: AIModelCapability,
    *,
    input_: str,
    output: str,
  ) -> None:
    if (
      input_ not in capability.input_modalities
      or output not in capability.output_modalities
    ):
      raise AICapabilityUnavailableError(
        f"AI model {model.id} {capability.type!r} does not support {input_!r} -> {output!r}"
      )

  @classmethod
  async def can_execute(
    cls,
    model: AIModelID,
    requirement: AIExecutionRequirement,
  ) -> bool:
    """Return static peer-local eligibility without probing a remote provider."""
    try:
      target = await cls._load_target_async(model)
      capability = cls._capability(target.model, requirement.capability)
    except (
      AIModelNotFoundError,
      AIModelDisabledError,
      AIProviderNotFoundError,
      AIProviderDisabledError,
      UnknownAIDialectError,
      InvalidAIProviderConfigError,
      AICapabilityUnavailableError,
    ):
      return False
    requested_inputs = requirement.input_modalities
    requested_outputs = requirement.output_modalities
    requested_features = requirement.features
    if not requested_inputs <= set(capability.input_modalities):
      return False
    if not requested_outputs <= set(capability.output_modalities):
      return False
    if not requested_features <= set(capability.features):
      return False
    if any(
      not target.adapter.supports_input_modality(requirement.capability, modality)
      for modality in requested_inputs
    ):
      return False
    if any(
      not target.adapter.supports_feature(requirement.capability, feature)
      for feature in requested_features
    ):
      return False
    return requirement.tool_choice is None or target.adapter.supports_tool_choice(
      requirement.tool_choice
    )

  @classmethod
  async def embed(
    cls,
    model: AIModelID,
    inputs: Sequence[str],
    dimensions: int,
  ) -> tuple[Vector, ...]:
    """Embed one ordered non-empty text batch through the selected model."""
    with operation(
      "ai.embed",
      kind=SpanKind.CLIENT,
      attributes={
        "gen_ai.operation.name": "embeddings",
        "inkcre.ai.model.id": model,
        "inkcre.ai.usage.input.source": "unavailable",
        "inkcre.ai.usage.output.source": "unavailable",
      },
    ) as observation:
      span = observation.span
      if not inputs:
        raise ValueError("embedding inputs must not be empty")
      if dimensions <= 0:
        raise ValueError("embedding dimensions must be positive")
      target = await cls._load_target_async(model)
      span.set_attributes(
        model_attributes(
          target.model.native_model_id, target.provider.dialect, target.model.provider
        )
      )
      capability = cls._capability(target.model, "embedding")
      cls._require_modalities(
        target.model,
        capability,
        input_="text",
        output="vector",
      )
      vectors = await target.adapter.embed(
        target.config,
        target.model.native_model_id,
        tuple(inputs),
        dimensions,
      )
      if len(vectors) != len(inputs):
        raise AIOutputContractError(
          f"Embedding result count {len(vectors)} does not match input count {len(inputs)}"
        )
      for vector in vectors:
        if len(vector) != dimensions:
          raise AIOutputContractError(
            f"Embedding dimension {len(vector)} does not match requested {dimensions}"
          )
        if not vector or not all(math.isfinite(value) for value in vector):
          raise AIOutputContractError("Embedding vectors must be finite and non-empty")
        if not any(value != 0 for value in vector):
          raise AIOutputContractError("Embedding vectors must be non-zero")
      return vectors

  @classmethod
  async def chat(
    cls,
    model: AIModelID,
    messages: Sequence[Message],
    tools: Sequence[FunctionTool] = (),
    tool_choice: ToolChoice | None = None,
  ) -> AssistantMessage:
    """Execute one provider-neutral chat model call without owning history."""
    with operation(
      "ai.chat",
      kind=SpanKind.CLIENT,
      attributes={
        "gen_ai.operation.name": "chat",
        "inkcre.ai.model.id": model,
        "inkcre.ai.usage.input.source": "unavailable",
        "inkcre.ai.usage.output.source": "unavailable",
      },
    ) as observation:
      span = observation.span
      history = validate_message_history(messages)
      target = await cls._load_target_async(model)
      span.set_attributes(
        model_attributes(
          target.model.native_model_id, target.provider.dialect, target.model.provider
        )
      )
      capability = cls._capability(target.model, "chat")
      # System instructions, Tool schemas/results and Assistant history all travel
      # through the textual chat channel even when the latest User turn is media-only.
      input_modalities: set[str] = {"text"}
      for message in history:
        if not isinstance(message, UserMessage):
          continue
        for part in message.content:
          if isinstance(part, TextContentPart):
            input_modalities.add("text")
          elif isinstance(part, ImageContentPart):
            input_modalities.add("image")
          elif isinstance(part, AudioContentPart):
            input_modalities.add("audio")
          elif isinstance(part, VideoContentPart):
            input_modalities.add("video")
      for modality in sorted(input_modalities):
        cls._require_modalities(
          target.model,
          capability,
          input_=modality,
          output="text",
        )
        if not target.adapter.supports_input_modality("chat", modality):
          raise AIInputUnavailableError(
            f"AI dialect {target.provider.dialect!r} cannot convey "
            f"chat modality {modality!r}"
          )

      tool_ids = tuple(tool.id for tool in tools)
      if len(tool_ids) != len(set(tool_ids)):
        raise ValueError("Tool IDs must be unique")
      requires_tool_calling = bool(tools) or tool_choice is not None
      if requires_tool_calling:
        if "tool_calling" not in capability.features or not target.adapter.supports_feature(
          "chat", "tool_calling"
        ):
          raise AIFeatureUnavailableError(
            f"AI model {model} and dialect {target.provider.dialect!r} do not jointly "
            "support tool_calling"
          )
      if tool_choice is not None:
        if not tools:
          raise ValueError("tool_choice requires at least one Tool")
        if not target.adapter.supports_tool_choice(tool_choice):
          raise AIFeatureUnavailableError(
            f"AI dialect {target.provider.dialect!r} cannot represent tool_choice"
          )

      return await target.adapter.chat(
        target.config,
        target.model.native_model_id,
        history,
        tuple(tools),
        tool_choice,
      )
