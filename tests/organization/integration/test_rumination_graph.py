"""Real PostgreSQL context and draft-to-submit graph proof."""

from inkcre_core.business.deployment_config import DeploymentConfigService
from tests.database import seed_block, seed_relation


import copy
import inspect
import json
import os
import typing
import uuid

import pytest
import sqlalchemy
import sqlmodel

from inkcre_core.business.agent import AgentManager
from inkcre_core.business.ai import AIManager
from inkcre_core.business.info_base.resolver import register_core_resolvers
from inkcre_core.business.organization import (
  DRAFT_GRAPH_TOOL,
  RUMINATION_CONFIG_KEY,
  RUMINATION_CONFIG_SCHEMA,
  SUBMIT_GRAPH_TOOL,
  RuminationBehaviorResolver,
)
from tests.database import TestSession
from inkcre_core.schemas import AgentDefinitionModel
from inkcre_core.schemas.ai import (
  AIModelModel,
  AIProviderModel,
  AssistantMessage,
  ChatCapability,
  JSONValue,
  TextContentPart,
  ToolCall,
  ToolResultMessage,
)
from inkcre_core.schemas.deployment_config import DeploymentConfigModel
from inkcre_core.schemas.info_base.block import BlockForm, BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)


def _cleanup(block_ids: list[int]) -> None:
  if not block_ids:
    return
  with TestSession() as db:
    db.connection().execute(
      sqlalchemy.text("DELETE FROM inkcre.blocks WHERE id = ANY(:ids)"),
      {"ids": block_ids},
    )
    db.commit()


async def _invoke(handler, input) -> JSONValue:
  result = handler(input)
  if inspect.isawaitable(result):
    result = await result
  return typing.cast(JSONValue, result)


def test_context_preserves_direction_and_draft_submit_maps_local_ids(async_runner):
  register_core_resolvers()
  block_ids: list[int] = []
  try:
    focal = seed_block(BlockForm(resolver="core.text.v1", content="A long focal document"))
    incoming = seed_block(BlockForm(resolver="core.text.v1", content="Incoming reference"))
    outgoing = seed_block(BlockForm(resolver="core.text.v1", content="Outgoing value"))
    block_ids.extend(
      block.id for block in (focal, incoming, outgoing) if block.id is not None
    )
    assert focal.id is not None and incoming.id is not None and outgoing.id is not None
    outgoing_relation = seed_relation(focal.id, outgoing.id, "highlight")
    incoming_relation = seed_relation(incoming.id, focal.id, "reference")

    message = async_runner.run(RuminationBehaviorResolver._build_initial_message(focal.id))
    assert message is not None
    text_part = message.content[0]
    assert isinstance(text_part, TextContentPart)
    context = json.loads(text_part.text)
    assert context["focal_block"] == {
      "id": focal.id,
      "resolver": "core.text.v1",
      "text": "A long focal document",
    }
    assert context["direct_relations"] == [
      {
        "id": outgoing_relation.id,
        "direction": "outgoing",
        "property": "highlight",
        "other_block": {
          "id": outgoing.id,
          "resolver": "core.text.v1",
          "label": "text <Outgoing value>",
        },
      },
      {
        "id": incoming_relation.id,
        "direction": "incoming",
        "property": "reference",
        "other_block": {
          "id": incoming.id,
          "resolver": "core.text.v1",
          "label": "text <Incoming reference>",
        },
      },
    ]

    tools = {
      tool.definition.id: tool
      for tool in AgentManager._bind_tools((DRAFT_GRAPH_TOOL, SUBMIT_GRAPH_TOOL))
    }

    async def draft_and_submit():
      draft = tools[DRAFT_GRAPH_TOOL]
      draft_input = draft.input_model.model_validate(
        {
          "resolver_type": "core.text.v1",
          "input": {"text": "Specific reusable insight"},
          "local_block_id_start": -11,
        }
      )
      graph = await _invoke(draft.handler, draft_input)
      assert isinstance(graph, dict)
      relations = graph["relations"]
      assert isinstance(relations, list)
      relations.append({"content": "interpretation", "from_": focal.id, "to_": -11})
      submit = tools[SUBMIT_GRAPH_TOOL]
      submit_input = submit.input_model.model_validate({"graph": graph})
      return await _invoke(submit.handler, submit_input)

    result = async_runner.run(draft_and_submit())
    assert isinstance(result, dict)
    result_blocks = result["blocks"]
    assert isinstance(result_blocks, list)
    mapping = result_blocks[0]
    assert isinstance(mapping, dict)
    assert mapping["local_id"] == -11
    block_ids.append(mapping["id"])
    with TestSession() as db:
      created = db.get(BlockModel, mapping["id"])
      relation = db.get(
        RelationModel,
        db.connection()
        .execute(
          sqlalchemy.text(
            "SELECT id FROM inkcre.relations "
            "WHERE from_ = :from AND to_ = :to AND content = 'interpretation'"
          ),
          {"from": focal.id, "to": mapping["id"]},
        )
        .scalar_one(),
      )
    assert created is not None
    assert created.content == "Specific reusable insight"
    assert relation is not None
  finally:
    _cleanup(block_ids)


def test_explicit_rumination_runs_real_agent_tools_and_repeats_additively(
  monkeypatch, async_runner
):
  register_core_resolvers()
  marker = uuid.uuid4().hex
  provider_id: int | None = None
  model_id: int | None = None
  agent_id: int | None = None
  block_ids: list[int] = []
  with TestSession() as db:
    existing_config = db.get(DeploymentConfigModel, RUMINATION_CONFIG_KEY)
    config_backup = existing_config.model_dump() if existing_config is not None else None

  try:
    with TestSession() as db:
      provider = AIProviderModel(
        name="Organization integration provider",
        dialect="core.openai-compatible.v1",
        config={"api_key": "unused"},
      )
      db.add(provider)
      db.flush()
      provider_id = provider.id
      assert provider_id is not None

      model = AIModelModel(
        provider=provider_id,
        native_model_id="organization-integration-model",
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
      model_id = model.id
      assert model_id is not None

      agent = AgentDefinitionModel(
        name="Organization integration Agent",
        system_prompt="Draft and submit one useful interpretation.",
        tools=(DRAFT_GRAPH_TOOL, SUBMIT_GRAPH_TOOL),
        tool_choice="auto",
        model=model_id,
        max_model_calls_per_turn=3,
      )
      db.add(agent)
      db.commit()
      db.refresh(agent)
      agent_id = agent.id
      assert agent_id is not None

    async_runner.run(
      DeploymentConfigService.replace(
        RUMINATION_CONFIG_KEY,
        RUMINATION_CONFIG_SCHEMA,
        {"agent": agent_id},
      )
    )
    focal = seed_block(
      BlockForm(resolver="core.text.v1", content=f"{marker}:coarse source document")
    )
    assert focal.id is not None
    block_ids.append(focal.id)
    model_calls = 0

    async def chat(_cls, model, messages, tools, tool_choice):
      nonlocal model_calls
      model_calls += 1
      assert model == model_id
      assert {tool.id for tool in tools} == {DRAFT_GRAPH_TOOL, SUBMIT_GRAPH_TOOL}
      assert tool_choice == "auto"
      if model_calls % 3 == 1:
        context = json.loads(messages[-1].content[0].text)
        assert context["focal_block"]["id"] == focal.id
        return AssistantMessage(
          tool_calls=(
            ToolCall(
              id=f"draft-{model_calls}",
              tool=DRAFT_GRAPH_TOOL,
              arguments={
                "resolver_type": "core.text.v1",
                "input": {"text": f"{marker}:specific insight {model_calls}"},
                "local_block_id_start": -1,
              },
            ),
          )
        )
      if model_calls % 3 == 2:
        result_message = messages[-1]
        assert isinstance(result_message, ToolResultMessage)
        assert not result_message.results[0].is_error
        graph = copy.deepcopy(result_message.results[0].content)
        assert isinstance(graph, dict)
        relations = graph["relations"]
        assert isinstance(relations, list)
        relations.append({"content": "interpretation", "from_": focal.id, "to_": -1})
        return AssistantMessage(
          tool_calls=(
            ToolCall(
              id=f"submit-{model_calls}",
              tool=SUBMIT_GRAPH_TOOL,
              arguments={"graph": graph},
            ),
          )
        )
      return AssistantMessage(content="complete")

    monkeypatch.setattr(AIManager, "chat", classmethod(chat))
    async_runner.run(RuminationBehaviorResolver.ruminate(focal.id))
    async_runner.run(RuminationBehaviorResolver.ruminate(focal.id))

    with TestSession() as db:
      derived = db.exec(
        sqlmodel.select(BlockModel).where(
          BlockModel.content.in_(  # pyrefly: ignore[missing-attribute]
            (f"{marker}:specific insight 1", f"{marker}:specific insight 4")
          )
        )
      ).all()
      derived_ids = [block.id for block in derived if block.id is not None]
      block_ids.extend(derived_ids)
      relations = db.exec(
        sqlmodel.select(RelationModel).where(
          RelationModel.from_ == focal.id,
          RelationModel.content == "interpretation",
        )
      ).all()
    assert len(derived_ids) == 2
    assert {relation.to_ for relation in relations} == set(derived_ids)
  finally:
    with TestSession() as db:
      current_config = db.get(DeploymentConfigModel, RUMINATION_CONFIG_KEY)
      if current_config is not None:
        db.delete(current_config)
        db.flush()
      if config_backup is not None:
        db.add(DeploymentConfigModel.model_validate(config_backup))
      if agent_id is not None:
        stored_agent = db.get(AgentDefinitionModel, agent_id)
        if stored_agent is not None:
          db.delete(stored_agent)
      if model_id is not None:
        stored_model = db.get(AIModelModel, model_id)
        if stored_model is not None:
          db.delete(stored_model)
      if provider_id is not None:
        stored_provider = db.get(AIProviderModel, provider_id)
        if stored_provider is not None:
          db.delete(stored_provider)
      db.commit()
    with TestSession() as db:
      block_ids.extend(
        block.id
        for block in db.exec(
          sqlmodel.select(BlockModel).where(
            BlockModel.content.like(f"{marker}:%")  # pyrefly: ignore[missing-attribute]
          )
        ).all()
        if block.id is not None
      )
    _cleanup(list(set(block_ids)))
