"""Alibaba Model Studio chat dialect, including native multimodal parts."""

from collections.abc import Sequence
import json
import typing
import time

from openai import AsyncStream
from openai.types.chat import ChatCompletionChunk
from opentelemetry import trace
from opentelemetry.trace import INVALID_SPAN
import pydantic

from inkcre_core.schemas.ai import (
  AudioContentPart,
  AssistantMessage,
  FunctionTool,
  ImageContentPart,
  Message,
  TextContentPart,
  ToolCall,
  ToolChoice,
  UserMessage,
  VideoContentPart,
)

from inkcre_core.libs.obsrv.telemetry import is_enabled

from ..contracts import AIOutputContractError
from ..main import AIManager
from ..telemetry import record_response_usage
from .openai_compatible import OpenAICompatibleConfig, OpenAICompatibleDialect


@AIManager.register_dialect(
  "core.alibaba-model-studio.v1",
  description="Alibaba Model Studio OpenAI-compatible multimodal chat protocol.",
  config_model=OpenAICompatibleConfig,
)
class AlibabaModelStudioDialect(OpenAICompatibleDialect):
  """Translate canonical chat through Model Studio's multimodal extensions."""

  supported_features = {"chat": frozenset({"tool_calling"})}
  supported_input_modalities = {
    "embedding": frozenset({"text"}),
    "chat": frozenset({"text", "image", "audio", "video"}),
  }

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
        result.append(
          {
            "type": "input_audio",
            "input_audio": {
              "data": cls._url_or_inline_data(
                mime_type=part.mime_type,
                data=part.data,
                transfer_url=part.transfer_url,
              ),
              "format": part.mime_type.removeprefix("audio/"),
            },
          }
        )
      elif isinstance(part, VideoContentPart):
        result.append(
          {
            "type": "video_url",
            "video_url": {
              "url": cls._url_or_inline_data(
                mime_type=part.mime_type,
                data=part.data,
                transfer_url=part.transfer_url,
              )
            },
          }
        )
    return result

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
      "stream": True,
      "stream_options": {"include_usage": True},
      "modalities": ["text"],
    }
    if tools:
      arguments["tools"] = self._tool_params(tools)
    if tool_choice is not None:
      arguments["tool_choice"] = self._tool_choice_param(tool_choice)

    span = trace.get_current_span() if is_enabled() else INVALID_SPAN
    span.set_attribute("gen_ai.request.stream", True)
    started = time.monotonic()
    usage = None
    finish_reasons: list[str] = []
    first_chunk = True
    try:
      stream = typing.cast(
        AsyncStream[ChatCompletionChunk],
        await client.chat.completions.create(**arguments),
      )
      text_parts: list[str] = []
      calls: dict[int, dict[str, str]] = {}
      async for chunk in stream:
        if first_chunk:
          span.set_attribute(
            "gen_ai.response.time_to_first_chunk", time.monotonic() - started
          )
          first_chunk = False
        # Usage-only terminal chunks have no choices. Totals replace earlier totals.
        if chunk.usage is not None:
          usage = chunk.usage
        if not chunk.choices:
          continue
        if chunk.choices[0].finish_reason is not None:
          finish_reasons = [chunk.choices[0].finish_reason]
        delta = chunk.choices[0].delta
        if isinstance(delta.content, str):
          text_parts.append(delta.content)
        for call in delta.tool_calls or ():
          current = calls.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
          if call.id:
            current["id"] = call.id
          if call.function is not None:
            if call.function.name:
              current["name"] += call.function.name
            if call.function.arguments:
              current["arguments"] += call.function.arguments
    finally:
      record_response_usage("chat", usage, finish_reasons)
      await client.close()

    parsed_calls: list[ToolCall] = []
    for index in sorted(calls):
      call = calls[index]
      if not call["id"] or not call["name"]:
        raise AIOutputContractError("Alibaba chat stream returned an incomplete ToolCall")
      try:
        arguments_object = json.loads(call["arguments"])
      except json.JSONDecodeError as error:
        raise AIOutputContractError(
          f"ToolCall {call['id']!r} arguments are not valid JSON"
        ) from error
      if not isinstance(arguments_object, dict):
        raise AIOutputContractError(
          f"ToolCall {call['id']!r} arguments must be a JSON object"
        )
      parsed_calls.append(
        ToolCall(id=call["id"], tool=call["name"], arguments=arguments_object)
      )
    content = "".join(text_parts) or None
    if content is None and not parsed_calls:
      raise AIOutputContractError("Alibaba chat stream returned no content or ToolCalls")
    return AssistantMessage(content=content, tool_calls=tuple(parsed_calls))
