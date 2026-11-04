"""Agent Tool binding and runtime failure contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import typing

import pydantic

from inkcre_core.schemas.ai import FunctionTool, JSONValue


AgentToolHandler: typing.TypeAlias = Callable[
  [pydantic.BaseModel],
  JSONValue | Awaitable[JSONValue],
]
AgentToolInputModelFactory: typing.TypeAlias = Callable[[], type[pydantic.BaseModel]]


class AgentError(RuntimeError):
  """Base failure exposed by the Agent domain."""


class AgentNotFoundError(AgentError):
  pass


class AgentToolBindingError(AgentError):
  pass


class DuplicateAgentToolRegistrationError(AgentToolBindingError):
  pass


class MissingAgentToolError(AgentToolBindingError):
  pass


class AgentTurnActiveError(AgentError):
  pass


class ToolExecutionError(Exception):
  """A Tool-owned actionable failure intended for the model."""

  def __init__(self, content: JSONValue):
    super().__init__("Agent Tool execution failed")
    self.content = content


@dataclass(frozen=True)
class BoundAgentTool:
  """One run-local Tool schema, validation model and code-owned handler."""

  definition: FunctionTool
  input_model: type[pydantic.BaseModel]
  handler: AgentToolHandler
