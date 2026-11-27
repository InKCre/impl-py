"""Real PostgreSQL context and draft-to-submit graph proof."""

from inkcre_core.business.deployment_config import DeploymentConfigService
from tests.database import seed_block, seed_relation


import copy
import json
import os
import uuid

import pytest
import sqlalchemy
import sqlmodel

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
    incoming = seed_block(BlockForm(resolver="core.text.v1", content="Incoming reference"))
    outgoing = seed_block(BlockForm(resolver="core.text.v1", content="Outgoing value"))
    assert incoming.id is not None and outgoing.id is not None
    block_ids.extend((incoming.id, outgoing.id))
    outgoing_relation = seed_relation(focal.id, outgoing.id, "highlight")
    incoming_relation = seed_relation(incoming.id, focal.id, "reference")
    model_calls = 0
    submitted_local_ids = []
    submitted_ids = []

    async def chat(_cls, model, messages, tools, tool_choice):
      nonlocal model_calls
      model_calls += 1
      assert model == model_id
      assert {tool.id for tool in tools} == {DRAFT_GRAPH_TOOL, SUBMIT_GRAPH_TOOL}
      assert tool_choice == "auto"
      if model_calls % 3 == 1:
        context = json.loads(messages[-1].content[0].text)
        assert context["focal_block"] == {
          "id": focal.id,
          "resolver": "core.text.v1",
          "text": f"{marker}:coarse source document",
        }
        if model_calls == 1:
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
        return AssistantMessage(
          tool_calls=(
            ToolCall(
              id=f"draft-{model_calls}",
              tool=DRAFT_GRAPH_TOOL,
              arguments={
                "resolver_type": "core.text.v1",
                "input": {"text": f"{marker}:specific insight {model_calls}"},
                "local_block_id_start": -11,
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
        relations.append({"content": "interpretation", "from_": focal.id, "to_": -11})
        return AssistantMessage(
          tool_calls=(
            ToolCall(
              id=f"submit-{model_calls}",
              tool=SUBMIT_GRAPH_TOOL,
              arguments={"graph": graph},
            ),
          )
        )
      result_message = messages[-1]
      assert isinstance(result_message, ToolResultMessage)
      assert not result_message.results[0].is_error
      payload = result_message.results[0].content
      assert isinstance(payload, dict)
      result_blocks = payload["blocks"]
      assert isinstance(result_blocks, list)
      mapping = result_blocks[0]
      assert isinstance(mapping, dict)
      submitted_local_ids.append(mapping["local_id"])
      persisted_id = mapping["id"]
      assert isinstance(persisted_id, int)
      submitted_ids.append(persisted_id)
      return AssistantMessage(content="complete")

    monkeypatch.setattr(AIManager, "chat", classmethod(chat))
    async_runner.run(RuminationBehaviorResolver.ruminate(focal.id))
    async_runner.run(RuminationBehaviorResolver.ruminate(focal.id))

    with TestSession() as db:
      for returned_id, expected_content in zip(
        submitted_ids,
        (f"{marker}:specific insight 1", f"{marker}:specific insight 4"),
        strict=True,
      ):
        returned_block = db.get(BlockModel, returned_id)
        assert returned_block is not None
        assert returned_block.content == expected_content
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
    assert submitted_local_ids == [-11, -11]
    assert len(derived_ids) == 2
    assert {relation.to_ for relation in relations} == set(submitted_ids)
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
