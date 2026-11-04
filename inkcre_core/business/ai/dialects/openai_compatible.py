"""OpenAI-compatible AI dialect across embedding and chat capabilities."""

import base64
from collections.abc import Callable, Sequence
import json
import typing

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
import pydantic

from inkcre_core.schemas.ai import (
  AudioContentPart,
  AssistantMessage,
  FunctionTool,
  ImageContentPart,
  Message,
  NamedToolChoice,
  SystemMessage,
  TextContentPart,
  ToolCall,
  ToolChoice,
  ToolResultMessage,
  UserMessage,
  VideoContentPart,
)
from inkcre_core.schemas.info_base.main import Vector

from ..contracts import AIDialectAdapter, AIInputUnavailableError, AIOutputContractError
from ..main import AIManager
from ..telemetry import record_response_usage

INLINE_MEDIA_MAX_BYTES = 7 * 1024 * 1024


class OpenAICompatibleConfig(pydantic.BaseModel):
  """Connection values for one OpenAI-compatible provider instance."""

  model_config = pydantic.ConfigDict(extra="forbid")

  api_key: pydantic.SecretStr
  base_url: str | None = pydantic.Field(default=None, min_length=1)


OpenAIClientFactory: typing.TypeAlias = Callable[[OpenAICompatibleConfig], AsyncOpenAI]


@AIManager.register_dialect(
  "core.openai-compatible.v1",
  description="OpenAI-compatible embedding and chat protocol.",
  config_model=OpenAICompatibleConfig,
)
class OpenAICompatibleDialect(AIDialectAdapter):
  """Translate canonical AI contracts through the OpenAI Python SDK."""

  supported_features = {"chat": frozenset({"tool_calling"})}
  supported_input_modalities = {
    "embedding": frozenset({"text"}),
    "chat": frozenset({"text", "image", "audio"}),
  }

  def __init__(self, client_factory: OpenAIClientFactory | None = None) -> None:
    self._client_factory = client_factory or self._create_client

  @staticmethod
  def _create_client(config: OpenAICompatibleConfig) -> AsyncOpenAI:
    return AsyncOpenAI(
      api_key=config.api_key.get_secret_value(),
      base_url=config.base_url,
    )

  @staticmethod
  def _config(config: pydantic.BaseModel) -> OpenAICompatibleConfig:
    return typing.cast(OpenAICompatibleConfig, config)

  async def embed(
    self,
    config: pydantic.BaseModel,
    native_model_id: str,
    inputs: Sequence[str],
    dimensions: int,
  ) -> tuple[Vector, ...]:
    client = self._client_factory(self._config(config))
    usage = None
    try:
      response = await client.embeddings.create(
        model=native_model_id,
        input=tuple(inputs),
        dimensions=dimensions,
        encoding_format="float",
      )
      usage = getattr(response, "usage", None)
    finally:
      record_response_usage("embeddings", usage)
      await client.close()

    by_index = {item.index: tuple(item.embedding) for item in response.data}
    expected = set(range(len(inputs)))
    if set(by_index) != expected:
      raise AIOutputContractError(
        "OpenAI-compatible embedding response indices do not cover the input batch"
      )
    return tuple(by_index[index] for index in range(len(inputs)))

  @staticmethod
  def _data_url(mime_type: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"

  @classmethod
  def _url_or_inline_data(
    cls,
    *,
    mime_type: str,
    data: bytes,
    transfer_url: str | None,
  ) -> str:
    if len(data) <= INLINE_MEDIA_MAX_BYTES:
      return cls._data_url(mime_type, data)
    if transfer_url is not None:
      return transfer_url
    raise AIInputUnavailableError(
      "Media exceeds the inline transfer bound and has no transfer URL"
    )

  @staticmethod
  def _audio_format(mime_type: str) -> str:
    formats = {
      "audio/mpeg": "mp3",
      "audio/mp3": "mp3",
      "audio/wav": "wav",
      "audio/x-wav": "wav",
    }
    try:
      return formats[mime_type]
    except KeyError as error:
      raise AIInputUnavailableError(
        f"OpenAI Chat input_audio does not support {mime_type!r}"
      ) from error

  @classmethod
  def _user_content_params(cls, message: UserMessage) -> list[dict[str, typing.Any]]:
    result: list[dict[str, typing.Any]] = []
    for part in message.content:
      if isinstance(part, TextContentPart):
        result.append({"type": "text", "text": part.text})
      elif isinstance(part, ImageContentPart):
        result.append(
          {
            "type": "image_url",
            "image_url": {
              "url": cls._url_or_inline_data(
                mime_type=part.mime_type,
                data=part.data,
                transfer_url=part.transfer_url,
              )
            },
          }
        )
      elif isinstance(part, AudioContentPart):
        if len(part.data) > INLINE_MEDIA_MAX_BYTES:
          raise AIInputUnavailableError(
            "OpenAI Chat input_audio cannot use a transfer URL and exceeds the "
            "inline transfer bound"
          )
        result.append(
          {
            "type": "input_audio",
            "input_audio": {
              "data": base64.b64encode(part.data).decode("ascii"),
              "format": cls._audio_format(part.mime_type),
            },
          }
        )
      elif isinstance(part, VideoContentPart):
        raise AIInputUnavailableError(
          "OpenAI Chat does not define a canonical video input part"
        )
    return result

  @classmethod
  def _message_params(cls, messages: Sequence[Message]) -> list[ChatCompletionMessageParam]:
    result: list[ChatCompletionMessageParam] = []
    for message in messages:
      if isinstance(message, SystemMessage):
        result.append({"role": "system", "content": message.content})
      elif isinstance(message, UserMessage):
        result.append(
          typing.cast(
            ChatCompletionMessageParam,
            {"role": "user", "content": cls._user_content_params(message)},
          )
        )
      elif isinstance(message, AssistantMessage):
        result.append(
          {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
              {
                "id": call.id,
                "type": "function",
                "function": {
                  "name": call.tool,
                  "arguments": json.dumps(
                    call.arguments,
                    separators=(",", ":"),
                    sort_keys=True,
                  ),
                },
              }
              for call in message.tool_calls
            ],
          }
        )
      elif isinstance(message, ToolResultMessage):
        result.extend(
          {
            "role": "tool",
            "tool_call_id": tool_result.tool_call_id,
            "content": json.dumps(
              {
                "content": tool_result.content,
                "is_error": tool_result.is_error,
              },
              separators=(",", ":"),
              sort_keys=True,
            ),
          }
          for tool_result in message.results
        )
    return result

  @staticmethod
  def _tool_params(tools: Sequence[FunctionTool]) -> list[dict[str, typing.Any]]:
    return [
      {
        "type": "function",
        "function": {
          "name": tool.id,
          "description": tool.description,
          "parameters": tool.input_schema,
        },
      }
      for tool in tools
    ]

  @staticmethod
  def _tool_choice_param(tool_choice: ToolChoice) -> typing.Any:
    if isinstance(tool_choice, NamedToolChoice):
      return {
        "type": "function",
        "function": {"name": tool_choice.tool},
      }
    return tool_choice

  def supports_tool_choice(self, tool_choice: ToolChoice) -> bool:
    return isinstance(tool_choice, NamedToolChoice) or tool_choice in {
      "none",
      "auto",
      "required",
    }

  @staticmethod
  def _assistant_message(message: typing.Any) -> AssistantMessage:
    calls: list[ToolCall] = []
    for call in message.tool_calls or ():
      if call.type != "function":
        raise AIOutputContractError(
          f"Unsupported OpenAI-compatible ToolCall type: {call.type!r}"
        )
      try:
        parsed_arguments = json.loads(call.function.arguments)
      except (TypeError, json.JSONDecodeError) as error:
        raise AIOutputContractError(
          f"ToolCall {call.id!r} arguments are not valid JSON"
        ) from error
      if not isinstance(parsed_arguments, dict):
        raise AIOutputContractError(f"ToolCall {call.id!r} arguments must be a JSON object")
      calls.append(
        ToolCall(
          id=call.id,
          tool=call.function.name,
          arguments=parsed_arguments,
        )
      )
    return AssistantMessage(content=message.content, tool_calls=tuple(calls))

  async def chat(
    self,
    config: pydantic.BaseModel,
    native_model_id: str,
    messages: Sequence[Message],
    tools: Sequence[FunctionTool],
    tool_choice: ToolChoice | None,
  ) -> AssistantMessage:
    client = self._client_factory(self._config(config))
    arguments: dict[str, typing.Any] = {
      "model": native_model_id,
      "messages": self._message_params(messages),
      "stream": False,
    }
    if tools:
      arguments["tools"] = self._tool_params(tools)
    if tool_choice is not None:
      arguments["tool_choice"] = self._tool_choice_param(tool_choice)

    usage = None
    finish_reasons: list[str] = []
    try:
      response = typing.cast(
        ChatCompletion,
        await client.chat.completions.create(**arguments),
      )
      usage = response.usage
      finish_reasons = [choice.finish_reason for choice in response.choices]
    finally:
      record_response_usage("chat", usage, finish_reasons)
      await client.close()

    if not response.choices:
      raise AIOutputContractError("OpenAI-compatible chat response has no choices")
    return self._assistant_message(response.choices[0].message)
