"""Canonical AI chat, Tool and ToolResult contracts."""

import typing
from urllib.parse import urlsplit

import pydantic


JSONValue: typing.TypeAlias = pydantic.JsonValue


class FunctionTool(pydantic.BaseModel):
  """One caller-owned function Tool exposed to an AI model."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  id: str = pydantic.Field(min_length=1)
  description: str
  input_schema: dict[str, JSONValue]


class ToolCall(pydantic.BaseModel):
  """One model-authored request to invoke a caller-owned Tool."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  id: str = pydantic.Field(min_length=1)
  tool: str = pydantic.Field(min_length=1)
  arguments: dict[str, JSONValue]


class ToolResult(pydantic.BaseModel):
  """One caller-side result correlated to a ToolCall."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  tool_call_id: str = pydantic.Field(min_length=1)
  content: JSONValue
  is_error: bool = False


class NamedToolChoice(pydantic.BaseModel):
  """Require one exact caller-owned Tool."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: typing.Literal["function"] = "function"
  tool: str = pydantic.Field(min_length=1)


ToolChoice: typing.TypeAlias = typing.Literal["none", "auto", "required"] | NamedToolChoice


class SystemMessage(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: typing.Literal["system"] = "system"
  content: str


class TextContentPart(pydantic.BaseModel):
  """One authored text item in an ordered multimodal User message."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: typing.Literal["text"] = "text"
  text: str = pydantic.Field(min_length=1)


class _MediaContentPart(pydantic.BaseModel):
  """AI-owned media transfer value, independent of Block and Storage identity."""

  model_config = pydantic.ConfigDict(
    extra="forbid",
    frozen=True,
    ser_json_bytes="base64",
    val_json_bytes="base64",
  )

  data: bytes = pydantic.Field(min_length=1)
  mime_type: str = pydantic.Field(min_length=1)
  transfer_url: str | None = None

  @pydantic.field_validator("mime_type")
  @classmethod
  def normalized_mime_type(cls, value: str) -> str:
    normalized = value.partition(";")[0].strip().lower()
    if "/" not in normalized or any(character.isspace() for character in normalized):
      raise ValueError("mime_type must be one concrete media type")
    return normalized

  @pydantic.field_validator("transfer_url")
  @classmethod
  def http_transfer_url(cls, value: str | None) -> str | None:
    if value is None:
      return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
      raise ValueError("transfer_url must be one absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
      raise ValueError("transfer_url must not contain user-info credentials")
    return value


class ImageContentPart(_MediaContentPart):
  type: typing.Literal["image"] = "image"

  @pydantic.field_validator("mime_type")
  @classmethod
  def image_mime_type(cls, value: str) -> str:
    if not value.startswith("image/"):
      raise ValueError("ImageContentPart requires an image MIME type")
    return value


class AudioContentPart(_MediaContentPart):
  type: typing.Literal["audio"] = "audio"

  @pydantic.field_validator("mime_type")
  @classmethod
  def audio_mime_type(cls, value: str) -> str:
    if not value.startswith("audio/"):
      raise ValueError("AudioContentPart requires an audio MIME type")
    return value


class VideoContentPart(_MediaContentPart):
  type: typing.Literal["video"] = "video"

  @pydantic.field_validator("mime_type")
  @classmethod
  def video_mime_type(cls, value: str) -> str:
    if not value.startswith("video/"):
      raise ValueError("VideoContentPart requires a video MIME type")
    return value


UserContentPart: typing.TypeAlias = typing.Annotated[
  TextContentPart | ImageContentPart | AudioContentPart | VideoContentPart,
  pydantic.Field(discriminator="type"),
]


class UserMessage(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(
    extra="forbid",
    frozen=True,
    ser_json_bytes="base64",
    val_json_bytes="base64",
  )

  type: typing.Literal["user"] = "user"
  content: tuple[UserContentPart, ...] = pydantic.Field(min_length=1)


class AssistantMessage(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: typing.Literal["assistant"] = "assistant"
  content: str | None = None
  tool_calls: tuple[ToolCall, ...] = ()

  @pydantic.model_validator(mode="after")
  def unique_tool_call_ids(self) -> "AssistantMessage":
    call_ids = tuple(call.id for call in self.tool_calls)
    if len(call_ids) != len(set(call_ids)):
      raise ValueError("AssistantMessage ToolCall IDs must be unique")
    return self


class ToolResultMessage(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: typing.Literal["tool_result"] = "tool_result"
  results: tuple[ToolResult, ...] = pydantic.Field(min_length=1)

  @pydantic.model_validator(mode="after")
  def unique_tool_call_ids(self) -> "ToolResultMessage":
    call_ids = tuple(result.tool_call_id for result in self.results)
    if len(call_ids) != len(set(call_ids)):
      raise ValueError("ToolResultMessage ToolCall IDs must be unique")
    return self


Message: typing.TypeAlias = typing.Annotated[
  SystemMessage | UserMessage | AssistantMessage | ToolResultMessage,
  pydantic.Field(discriminator="type"),
]

_MESSAGES_ADAPTER = pydantic.TypeAdapter(tuple[Message, ...])


def validate_message_history(value: typing.Any) -> tuple[Message, ...]:
  """Validate canonical types and every closed Assistant/ToolResult pair."""
  messages = _MESSAGES_ADAPTER.validate_python(value)
  index = 0
  while index < len(messages):
    message = messages[index]
    if isinstance(message, ToolResultMessage):
      raise ValueError("ToolResultMessage must follow an AssistantMessage with ToolCalls")
    if isinstance(message, AssistantMessage) and message.tool_calls:
      if index + 1 >= len(messages) or not isinstance(
        messages[index + 1], ToolResultMessage
      ):
        raise ValueError("AssistantMessage ToolCalls require an adjacent ToolResultMessage")
      result_message = typing.cast(ToolResultMessage, messages[index + 1])
      call_ids = {call.id for call in message.tool_calls}
      result_ids = {result.tool_call_id for result in result_message.results}
      if result_ids != call_ids:
        raise ValueError("ToolResultMessage must exactly cover preceding ToolCall IDs")
      index += 2
      continue
    index += 1
  return messages
