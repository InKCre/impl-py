"""Development traces preserve real Turn outcomes and tool feedback."""

import asyncio
import json
import logging
import uuid

import pydantic
import pytest

from inkcre_core.business.agent import (
  AgentTurnActiveError,
  BoundAgentTool,
  InMemoryThreadPersistenceBackend,
  Thread,
  ThreadState,
  TurnTermination,
)
from inkcre_core.business.ai import AIManager
from inkcre_core.schemas.ai import (
  AssistantMessage,
  FunctionTool,
  SystemMessage,
  ToolCall,
  UserMessage,
  TextContentPart,
)
from dataclasses import replace
from inkcre_core import current_core
from inkcre_core.libs.obsrv.log_record import TRACE_ID


class _Input(pydantic.BaseModel):
  value: int


@pytest.mark.parametrize("budget", [1, 2])
def test_trace_retains_tool_errors_and_actual_budget_outcome(monkeypatch, caplog, budget):
  monkeypatch.setattr(
    current_core(), "options", replace(current_core().options, agent_debug=True)
  )
  caplog.set_level(logging.INFO, logger="inkcre")
  invoked = []

  async def handler(input):
    invoked.append(input.value)
    raise ValueError("diagnostic tool failure")

  calls = 0

  async def chat(cls, *args):
    nonlocal calls
    calls += 1
    if calls == 1:
      return AssistantMessage(
        tool_calls=(
          ToolCall(id="bad-input", tool="sample", arguments={"value": "invalid"}),
          ToolCall(id="failure", tool="sample", arguments={"value": 7}),
        )
      )
    return AssistantMessage(content="finished")

  monkeypatch.setattr(AIManager, "chat", classmethod(chat))

  async def run():
    backend = InMemoryThreadPersistenceBackend()
    tool = BoundAgentTool(
      definition=FunctionTool(
        id="sample", description="sample", input_schema=_Input.model_json_schema()
      ),
      input_model=_Input,
      handler=handler,
    )
    ident, state = await backend.create(
      ThreadState(
        model=1,
        tools=(tool.definition,),
        tool_choice="auto",
        max_model_calls_per_turn=budget,
        messages=(SystemMessage(content="system"),),
      )
    )
    thread = Thread(ident, state, backend, (tool,))
    token = TRACE_ID.set("job.debug-example")
    try:
      outcome = await thread.start_turn(
        UserMessage(content=(TextContentPart(text="input"),))
      )
    finally:
      TRACE_ID.reset(token)
    return outcome, str(ident)

  outcome, ident = asyncio.run(run())
  assert invoked == [7]
  assert outcome == (
    TurnTermination.MAX_MODEL_CALLS if budget == 1 else TurnTermination.COMPLETED
  )
  events = [
    json.loads(r.getMessage()) for r in caplog.records if r.name == "inkcre.agent.debug"
  ]
  assert all(
    e["thread_id"] == ident and e["trace_id"] == "job.debug-example" for e in events
  )
  finished = [e for e in events if e["event"] == "agent.tool.completed"]
  assert {e["result"]["tool_call_id"] for e in finished} == {"bad-input", "failure"}
  assert all(e["result"]["is_error"] for e in finished)
  assert any(e.get("error") == "diagnostic tool failure" for e in events)
  assert events[-1]["outcome"] == outcome
  assert events[-1]["model_calls"] == budget


def test_trace_records_cancellation_without_turn_recovery(monkeypatch, caplog):
  monkeypatch.setattr(
    current_core(), "options", replace(current_core().options, agent_debug=True)
  )
  caplog.set_level(logging.INFO, logger="inkcre")

  async def run():
    entered = asyncio.Event()

    async def chat(cls, _model, messages, _tools, _choice):
      if not entered.is_set():
        entered.set()
        await asyncio.Event().wait()
      # Cancellation leaves the accepted User input; the next explicit turn
      # must not replay the abandoned call or write a synthetic assistant reply.
      assert [message.type for message in messages] == ["user", "user"]
      assert messages[-1].content == (TextContentPart(text="next input"),)
      return AssistantMessage(content="next answer")

    monkeypatch.setattr(AIManager, "chat", classmethod(chat))
    backend = InMemoryThreadPersistenceBackend()
    ident, state = await backend.create(
      ThreadState(
        model=1, tools=(), tool_choice=None, max_model_calls_per_turn=2, messages=()
      )
    )
    thread = Thread(ident, state, backend, ())
    task = thread.start_turn(UserMessage(content=(TextContentPart(text="input"),)))
    async with asyncio.timeout(5):
      await entered.wait()
      with pytest.raises(AgentTurnActiveError, match="already has an active Turn"):
        thread.start_turn(UserMessage(content=(TextContentPart(text="concurrent"),)))
      task.cancel()
      with pytest.raises(asyncio.CancelledError):
        await task
      state = await thread.refresh()
      assert [message.type for message in state.messages] == ["user"]
      assert (
        await thread.start_turn(UserMessage(content=(TextContentPart(text="next input"),)))
        == TurnTermination.COMPLETED
      )

  asyncio.run(run())
  finished = [
    json.loads(r.getMessage())
    for r in caplog.records
    if r.name == "inkcre.agent.debug" and r.event == "agent.turn.finished"
  ]
  assert [(event["outcome"], event["model_calls"]) for event in finished] == [
    ("cancelled", 1),
    ("completed", 1),
  ]


def test_debug_disabled_suppresses_output_and_broken_destination_preserves_outcome(
  monkeypatch, caplog, capsys
):
  from inkcre_core.business.agent.debug import trace

  monkeypatch.setattr(
    current_core(), "options", replace(current_core().options, agent_debug=False)
  )
  caplog.set_level(logging.INFO, logger="inkcre")
  asyncio.run(trace("disabled", uuid.uuid4(), value="valid payload"))
  assert not any(record.name == "inkcre.agent.debug" for record in caplog.records)
  monkeypatch.setattr(
    current_core(), "options", replace(current_core().options, agent_debug=True)
  )
  asyncio.run(trace("example", uuid.uuid4(), unsupported=object()))
  assert "TypeError" in capsys.readouterr().err
  from inkcre_core.libs.obsrv.main import get_logger

  def broken_destination(*args, **kwargs):
    raise OSError("destination unavailable")

  monkeypatch.setattr(get_logger().getChild("agent.debug"), "info", broken_destination)
  asyncio.run(trace("example", uuid.uuid4(), value="valid payload"))
  assert "OSError" in capsys.readouterr().err
