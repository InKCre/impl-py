"""Replaceable whole-Thread persistence with an in-memory MVP backend."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
import typing
import uuid

import pydantic

from inkcre_core.schemas.ai import (
  AIModelID,
  AssistantMessage,
  FunctionTool,
  Message,
  ToolChoice,
  validate_message_history,
)


ThreadID: typing.TypeAlias = uuid.UUID


def validate_thread_message_history(value: typing.Any) -> tuple[Message, ...]:
  """Allow strict history plus one recoverable trailing ToolCall message."""
  messages = tuple(value)
  if messages and isinstance(messages[-1], AssistantMessage) and messages[-1].tool_calls:
    validate_message_history(messages[:-1])
    return messages
  return validate_message_history(messages)


class ThreadState(pydantic.BaseModel):
  """Serializable Thread snapshot owned by a persistence backend."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  model: AIModelID
  tools: tuple[FunctionTool, ...]
  tool_choice: ToolChoice | None
  max_model_calls_per_turn: int = pydantic.Field(gt=0)
  messages: tuple[Message, ...]

  @pydantic.field_validator("tools")
  @classmethod
  def unique_tools(cls, value: tuple[FunctionTool, ...]) -> tuple[FunctionTool, ...]:
    tool_ids = tuple(tool.id for tool in value)
    if len(tool_ids) != len(set(tool_ids)):
      raise ValueError("Thread Tool IDs must be unique")
    return value

  @pydantic.field_validator("messages")
  @classmethod
  def closed_or_recoverable_history(cls, value: tuple[Message, ...]) -> tuple[Message, ...]:
    return validate_thread_message_history(value)


class ThreadPersistenceBackend(ABC):
  """Atomic persistence boundary for complete Thread snapshots."""

  @abstractmethod
  async def create(self, state: ThreadState) -> tuple[ThreadID, ThreadState]:
    raise NotImplementedError

  @abstractmethod
  async def read(self, thread_id: ThreadID) -> ThreadState:
    raise NotImplementedError

  @abstractmethod
  async def append(
    self,
    thread_id: ThreadID,
    messages: tuple[Message, ...],
  ) -> ThreadState:
    raise NotImplementedError

  @abstractmethod
  async def discard_trailing_incomplete_tool_calls(
    self,
    thread_id: ThreadID,
  ) -> ThreadState:
    raise NotImplementedError


class InMemoryThreadPersistenceBackend(ThreadPersistenceBackend):
  """Process-local backend preserving atomic Thread message updates."""

  def __init__(self) -> None:
    self._states: dict[ThreadID, ThreadState] = {}
    self._locks: dict[ThreadID, asyncio.Lock] = {}

  async def create(self, state: ThreadState) -> tuple[ThreadID, ThreadState]:
    thread_id = uuid.uuid4()
    self._states[thread_id] = state
    self._locks[thread_id] = asyncio.Lock()
    return thread_id, state

  async def read(self, thread_id: ThreadID) -> ThreadState:
    try:
      return self._states[thread_id]
    except KeyError as error:
      raise KeyError(f"Thread {thread_id} does not exist") from error

  async def append(
    self,
    thread_id: ThreadID,
    messages: tuple[Message, ...],
  ) -> ThreadState:
    async with self._lock(thread_id):
      current = await self.read(thread_id)
      updated = ThreadState(
        model=current.model,
        tools=current.tools,
        tool_choice=current.tool_choice,
        max_model_calls_per_turn=current.max_model_calls_per_turn,
        messages=current.messages + messages,
      )
      self._states[thread_id] = updated
      return updated

  async def discard_trailing_incomplete_tool_calls(
    self,
    thread_id: ThreadID,
  ) -> ThreadState:
    async with self._lock(thread_id):
      current = await self.read(thread_id)
      if not current.messages:
        return current
      trailing = current.messages[-1]
      if not isinstance(trailing, AssistantMessage) or not trailing.tool_calls:
        return current
      updated = ThreadState(
        model=current.model,
        tools=current.tools,
        tool_choice=current.tool_choice,
        max_model_calls_per_turn=current.max_model_calls_per_turn,
        messages=current.messages[:-1],
      )
      self._states[thread_id] = updated
      return updated

  def _lock(self, thread_id: ThreadID) -> asyncio.Lock:
    try:
      return self._locks[thread_id]
    except KeyError as error:
      raise KeyError(f"Thread {thread_id} does not exist") from error
