"""Cancellable Agent Thread and per-turn model/Tool orchestration."""

from __future__ import annotations

import asyncio
from enum import StrEnum
import inspect
import hashlib
import json
import time
import traceback
import typing

import pydantic

from inkcre_core.business.ai import AIManager
from inkcre_core.schemas.ai import (
  AssistantMessage,
  JSONValue,
  ToolCall,
  ToolResult,
  ToolResultMessage,
  UserMessage,
)

from .contracts import AgentTurnActiveError, BoundAgentTool, ToolExecutionError
from .persistence import ThreadID, ThreadPersistenceBackend, ThreadState
from .debug import trace
from inkcre_core.libs.obsrv.main import get_logger
from inkcre_core.libs.obsrv.telemetry import operation


logger = get_logger().getChild("agent.thread")


class TurnTermination(StrEnum):
  COMPLETED = "completed"
  MAX_MODEL_CALLS = "max_model_calls"


class Thread:
  """One persisted Agent-definition snapshot with at most one active Turn."""

  def __init__(
    self,
    thread_id: ThreadID,
    state: ThreadState,
    persistence: ThreadPersistenceBackend,
    tools: tuple[BoundAgentTool, ...],
  ) -> None:
    self.id = thread_id
    self._state = state
    self._persistence = persistence
    self._tools = {tool.definition.id: tool for tool in tools}
    self.current_turn: asyncio.Task[TurnTermination] | None = None
    self._turn_index = 0
    self._model_calls = 0

  @property
  def messages(self):
    return self._state.messages

  @property
  def model(self):
    return self._state.model

  @property
  def tools(self):
    return self._state.tools

  @property
  def tool_choice(self):
    return self._state.tool_choice

  @property
  def max_model_calls_per_turn(self):
    return self._state.max_model_calls_per_turn

  async def refresh(self) -> ThreadState:
    """Refresh the local view from the persistence authority."""
    self._state = await self._persistence.read(self.id)
    return self._state

  def start_turn(self, input: UserMessage) -> asyncio.Task[TurnTermination]:
    """Schedule one complete model/Tool turn and return its cancellation handle."""
    if self.current_turn is not None and not self.current_turn.done():
      raise AgentTurnActiveError(f"Thread {self.id} already has an active Turn")
    self.current_turn = asyncio.create_task(
      self._run_turn(input),
      name=f"agent-thread-{self.id}",
    )
    return self.current_turn

  async def _run_turn(self, input: UserMessage) -> TurnTermination:
    self._turn_index += 1
    self._model_calls = 0
    with operation(
      "agent.turn",
      attributes={
        "gen_ai.conversation.id": str(self.id),
        "inkcre.agent.turn": self._turn_index,
      },
    ) as observation:
      span = observation.span
      started = time.monotonic()
      await trace(
        "agent.turn.started",
        self.id,
        turn=self._turn_index,
        input=input,
        model=self.model,
        max_model_calls=self.max_model_calls_per_turn,
      )
      try:
        outcome = await self._execute_turn(input)
      except asyncio.CancelledError:
        span.set_attribute("inkcre.agent.outcome", "cancelled")
        await trace(
          "agent.turn.finished",
          self.id,
          turn=self._turn_index,
          model_calls=self._model_calls,
          outcome="cancelled",
          elapsed_seconds=time.monotonic() - started,
        )
        raise
      except Exception as error:
        span.set_attribute("inkcre.agent.outcome", "failed")
        await trace(
          "agent.turn.finished",
          self.id,
          turn=self._turn_index,
          model_calls=self._model_calls,
          outcome="failed",
          error_type=type(error).__name__,
          error=str(error),
          traceback=traceback.format_exc(),
          elapsed_seconds=time.monotonic() - started,
        )
        raise
      await trace(
        "agent.turn.finished",
        self.id,
        turn=self._turn_index,
        model_calls=self._model_calls,
        outcome=outcome,
        elapsed_seconds=time.monotonic() - started,
      )
      span.set_attribute("inkcre.agent.outcome", outcome.value)
      span.set_attribute("inkcre.agent.model_calls", self._model_calls)
      return outcome

  async def _execute_turn(self, input: UserMessage) -> TurnTermination:
    self._state = await self._persistence.discard_trailing_incomplete_tool_calls(self.id)
    self._state = await self._persistence.append(self.id, (input,))

    while True:
      self._model_calls += 1
      with operation(
        "agent.step",
        attributes={
          "gen_ai.conversation.id": str(self.id),
          "inkcre.agent.turn": self._turn_index,
          "inkcre.agent.model_call": self._model_calls,
        },
      ):
        await trace(
          "agent.model.started",
          self.id,
          turn=self._turn_index,
          call=self._model_calls,
        )
        started = time.monotonic()
        assistant = await AIManager.chat(
          self._state.model,
          self._state.messages,
          self._state.tools,
          self._state.tool_choice,
        )
        await trace(
          "agent.model.completed",
          self.id,
          turn=self._turn_index,
          call=self._model_calls,
          response=assistant,
          elapsed_seconds=time.monotonic() - started,
        )
        if not assistant.tool_calls:
          self._state = await self._persistence.append(self.id, (assistant,))
          return TurnTermination.COMPLETED

        results = await self._execute_tool_batch(assistant)
        self._state = await self._persistence.append(
          self.id,
          (assistant, ToolResultMessage(results=results)),
        )
        if self._model_calls >= self._state.max_model_calls_per_turn:
          return TurnTermination.MAX_MODEL_CALLS

  async def _execute_tool_batch(
    self,
    assistant: AssistantMessage,
  ) -> tuple[ToolResult, ...]:
    tasks = tuple(
      asyncio.create_task(
        self._execute_tool_call(call),
        name=f"agent-tool-{call.tool}-{call.id}",
      )
      for call in assistant.tool_calls
    )
    return tuple(await asyncio.gather(*tasks))

  async def _execute_tool_call(self, call: ToolCall) -> ToolResult:
    with operation(
      "agent.tool",
      attributes={
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.conversation.id": str(self.id),
        "inkcre.agent.turn": self._turn_index,
        "inkcre.agent.model_call": self._model_calls,
        # Provider-generated IDs may contain content; retain correlation as a digest.
        "inkcre.agent.tool_call.id_hash": hashlib.sha256(
          call.id.encode(errors="replace")
        ).hexdigest(),
      },
    ) as observation:
      span = observation.span
      tool = self._tools.get(call.tool)
      if tool is not None:
        span.set_attribute("gen_ai.tool.name", tool.definition.id)
      await trace(
        "agent.tool.started",
        self.id,
        turn=self._turn_index,
        call=self._model_calls,
        tool_call=call,
      )
      started = time.monotonic()
      try:
        result = await self._invoke_tool_call(call)
      except asyncio.CancelledError:
        span.set_attribute("inkcre.agent.outcome", "cancelled")
        await trace(
          "agent.tool.cancelled",
          self.id,
          turn=self._turn_index,
          call=self._model_calls,
          tool_call_id=call.id,
          tool=call.tool,
          elapsed_seconds=time.monotonic() - started,
        )
        raise
      await trace(
        "agent.tool.completed",
        self.id,
        turn=self._turn_index,
        call=self._model_calls,
        tool=call.tool,
        result=result,
        elapsed_seconds=time.monotonic() - started,
      )
      span.set_attribute(
        "inkcre.agent.outcome", "error" if result.is_error else "completed"
      )
      if result.is_error:
        span.set_attribute("error.type", "ToolResultError")
        observation.outcome = "error"
      return result

  async def _invoke_tool_call(self, call: ToolCall) -> ToolResult:
    tool = self._tools.get(call.tool)
    if tool is None:
      return ToolResult(
        tool_call_id=call.id,
        content={"error": "unknown_tool", "tool": call.tool},
        is_error=True,
      )

    try:
      tool_input = tool.input_model.model_validate(call.arguments)
    except pydantic.ValidationError as error:
      return ToolResult(
        tool_call_id=call.id,
        content=typing.cast(JSONValue, json.loads(error.json())),
        is_error=True,
      )

    try:
      content = tool.handler(tool_input)
      if inspect.isawaitable(content):
        content = await content
      return ToolResult(tool_call_id=call.id, content=content)
    except ToolExecutionError as error:
      return ToolResult(
        tool_call_id=call.id,
        content=error.content,
        is_error=True,
      )
    except Exception as error:
      await trace(
        "agent.tool.exception",
        self.id,
        turn=self._turn_index,
        call=self._model_calls,
        tool_call_id=call.id,
        tool=call.tool,
        error_type=type(error).__name__,
        error=str(error),
        traceback=traceback.format_exc(),
      )
      logger.exception("Unexpected Agent Tool failure", extra={"tool": call.tool})
      return ToolResult(
        tool_call_id=call.id,
        content={"error": "tool_execution_failed"},
        is_error=True,
      )
