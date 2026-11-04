"""Real PostgreSQL proof for Agent definitions and AgentManager snapshots."""

import datetime
import os
import time

import pydantic
import pytest
import sqlalchemy
import sqlalchemy.exc

from inkcre_core.business.agent import (
  AgentManager,
  InMemoryThreadPersistenceBackend,
  TurnTermination,
)
from inkcre_core.business.ai import AIManager
from tests.database import TestSession
from inkcre_core.schemas import AgentDefinitionModel
from inkcre_core.schemas.ai import (
  AIModelModel,
  AIProviderModel,
  AssistantMessage,
  ChatCapability,
  NamedToolChoice,
  TextContentPart,
  UserMessage,
)


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)


class _ProbeInput(pydantic.BaseModel):
  value: int


class _IntegrationAgentManager(AgentManager):
  _TOOLS = {}
  _persistence = InMemoryThreadPersistenceBackend()


@_IntegrationAgentManager.tool("alpha", description="Return a probe value")
async def _alpha(input: _ProbeInput):
  return {"value": input.value}


@_IntegrationAgentManager.tool("zeta", description="Return another probe value")
async def _zeta(input: _ProbeInput):
  return {"value": input.value}


def _cleanup(provider_id: int | None) -> None:
  if provider_id is None:
    return
  with TestSession() as db:
    db.connection().execute(
      sqlalchemy.text(
        "DELETE FROM inkcre.agents WHERE model IN "
        "(SELECT id FROM inkcre.ai_models WHERE provider = :provider)"
      ),
      {"provider": provider_id},
    )
    db.connection().execute(
      sqlalchemy.text("DELETE FROM inkcre.ai_models WHERE provider = :provider"),
      {"provider": provider_id},
    )
    db.connection().execute(
      sqlalchemy.text("DELETE FROM inkcre.ai_providers WHERE id = :provider"),
      {"provider": provider_id},
    )
    db.commit()


def test_agent_definition_round_trip_and_active_thread_snapshot(monkeypatch, async_runner):
  async_runner.run(AIManager.sync_dialects_async())
  provider_id: int | None = None
  try:
    with TestSession() as db:
      provider = AIProviderModel(
        name="Agent integration provider",
        dialect="core.openai-compatible.v1",
        config={"api_key": "unused"},
      )
      db.add(provider)
      db.flush()
      provider_id = provider.id
      assert provider_id is not None

      model = AIModelModel(
        provider=provider_id,
        native_model_id="agent-integration-model",
        capabilities=(
          ChatCapability(
            input_modalities=["text"],
            output_modalities=["text"],
            features=["tool_calling"],
          ),
        ),
      )
      db.add(model)
      db.flush()
      assert model.id is not None

      agent = AgentDefinitionModel(
        name="Integration Agent",
        system_prompt="Original system prompt",
        tools=("zeta", "alpha"),
        tool_choice=NamedToolChoice(tool="alpha"),
        model=model.id,
        max_model_calls_per_turn=2,
      )
      db.add(agent)
      db.commit()
      db.refresh(agent)
      assert agent.id is not None
      agent_id = agent.id

      assert agent.tools == ("alpha", "zeta")
      assert isinstance(agent.tool_choice, NamedToolChoice)
      assert agent.tool_choice.tool == "alpha"
      assert agent.created_at.tzinfo is not None
      previous_updated_at = agent.updated_at
      time.sleep(0.002)
      agent.name = "Updated Integration Agent"
      db.add(agent)
      db.commit()
      db.refresh(agent)
      assert isinstance(agent.updated_at, datetime.datetime)
      assert agent.updated_at > previous_updated_at

    async def scenario():
      async def chat(_cls, model, messages, tools, tool_choice):
        assert [tool.id for tool in tools] == ["alpha", "zeta"]
        assert isinstance(tool_choice, NamedToolChoice)
        return AssistantMessage(content="complete")

      monkeypatch.setattr(AIManager, "chat", classmethod(chat))
      thread = await _IntegrationAgentManager.run(
        agent_id,
        UserMessage(content=(TextContentPart(text="initial input"),)),
      )
      assert thread.current_turn is not None
      assert await thread.current_turn == TurnTermination.COMPLETED
      assert thread.messages[0].content == "Original system prompt"
      assert [message.type for message in thread.messages] == [
        "system",
        "user",
        "assistant",
      ]

    async_runner.run(scenario())

    with TestSession() as db:
      stored_agent = db.get(AgentDefinitionModel, agent_id)
      assert stored_agent is not None
      model_id = stored_agent.model
      with pytest.raises(sqlalchemy.exc.IntegrityError):
        db.connection().execute(
          sqlalchemy.text(
            "INSERT INTO inkcre.agents "
            "(name, system_prompt, tools, model, max_model_calls_per_turn) "
            "VALUES ('invalid', 'prompt', '{}', :model, 0)"
          ),
          {"model": model_id},
        )
      db.rollback()
  finally:
    _cleanup(provider_id)
