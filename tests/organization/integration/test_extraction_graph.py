"""Actual Agent tools and PostgreSQL prove retained evidence and replay behavior."""

import asyncio
import json
import os
import uuid

import pytest
import sqlalchemy

from app.business.ai import AIManager
from app.business.deployment_config import DeploymentConfigService
from app.business.info_base.resolver import (
  ResolverManager,
  register_core_resolvers,
)
from app.business.organization import CREATE_EXTRACTION_TOOL, READ_EXTRACTION_MATERIAL_TOOL
from app.business.organization.extraction import (
  EXTRACTION_CONFIG_KEY,
  EXTRACTION_CONFIG_SCHEMA,
  EXTRACTION_RELATION,
  ExtractionBehaviorResolver,
)
from app.persistence.info_base.repository import RelationRepository
from app.persistence.info_base.uow import graph_uow
from app.schemas import AgentDefinitionModel
from app.schemas.ai import (
  AIModelModel,
  AIProviderModel,
  AssistantMessage,
  ChatCapability,
  ToolCall,
  ToolResultMessage,
)
from app.schemas.deployment_config import DeploymentConfigModel
from app.schemas.info_base.block import BlockForm
from app.schemas.organization_extraction import (
  ExtractionProposal,
  ExtractionWriteResult,
)
from tests.database import (
  TestSession,
  read_block,
  read_relations,
  seed_block,
)


pytestmark = [
  pytest.mark.integration,
  pytest.mark.skipif(
    not os.getenv("INKCRE_TEST_DATABASE_URL"), reason="requires PostgreSQL"
  ),
]


@pytest.fixture
def extraction_world(async_runner):
  register_core_resolvers()
  marker = uuid.uuid4().hex
  with TestSession() as db:
    existing = db.get(DeploymentConfigModel, EXTRACTION_CONFIG_KEY)
    backup = existing.model_dump() if existing is not None else None
    provider = AIProviderModel(
      name=marker, dialect="core.openai-compatible.v1", config={"api_key": "unused"}
    )
    db.add(provider)
    db.flush()
    model = AIModelModel(
      provider=provider.id,
      native_model_id=marker,
      capabilities=(
        ChatCapability(
          input_modalities=["text"], output_modalities=["text"], features=["tool_calling"]
        ),
      ),
    )
    db.add(model)
    db.flush()
    agent = AgentDefinitionModel(
      name=marker,
      model=model.id,
      system_prompt="Extract qualified advice with exact source excerpts.",
      tools=(READ_EXTRACTION_MATERIAL_TOOL, CREATE_EXTRACTION_TOOL),
      tool_choice="auto",
      max_model_calls_per_turn=4,
    )
    db.add(agent)
    db.commit()
  try:
    async_runner.run(
      DeploymentConfigService.replace(
        EXTRACTION_CONFIG_KEY, EXTRACTION_CONFIG_SCHEMA, {"agent": agent.id}
      )
    )
    question = seed_block(
      BlockForm(
        resolver="core.text.v1",
        content=f"{marker}: Alice is learning Python with packaging experience.",
      )
    )
    answer = seed_block(
      BlockForm(
        resolver="core.text.v1",
        content=f"{marker}: Bob suggests packaging issues, not a requirement.",
      )
    )
    yield marker, question, answer
  finally:
    with TestSession() as db:
      db.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.blocks WHERE content LIKE :marker"),
        {"marker": f"%{marker}%"},
      )
      config = db.get(DeploymentConfigModel, EXTRACTION_CONFIG_KEY)
      if config is not None:
        db.delete(config)
        db.flush()
      if backup is not None:
        db.add(DeploymentConfigModel.model_validate(backup))
      for row in (agent, model, provider):
        current = db.get(type(row), row.id)
        if current is not None:
          db.delete(current)
          db.flush()
      db.commit()


def test_source_identity_and_deletion_boundaries(
  extraction_world, monkeypatch, async_runner
):
  marker, question, answer = extraction_world
  copied = seed_block(BlockForm(resolver="core.text.v1", content=question.content))
  assert copied.id is not None
  completed: list[ExtractionWriteResult] = []
  remove_before_submit = False

  async def chat(_cls, _model, messages, _tools, _choice):
    if isinstance(messages[-1], ToolResultMessage):
      result = messages[-1].results[0]
      if remove_before_submit:
        assert result.is_error
        assert "no longer exists" in json.dumps(result.content)
      else:
        assert not result.is_error
        completed.append(ExtractionWriteResult.model_validate(result.content))
      return AssistantMessage(content="complete")
    material = _material(messages)
    if remove_before_submit:
      async with graph_uow() as uow:
        await uow.blocks.delete(material["source_block_id"])
    return AssistantMessage(
      tool_calls=(
        ToolCall(
          id="submit",
          tool=CREATE_EXTRACTION_TOOL,
          arguments={
            "text": f"{marker}: Independently attributable information.",
            "sources": [
              {"observation_id": material["observation_id"], "excerpts": [material["text"]]}
            ],
          },
        ),
      )
    )

  monkeypatch.setattr(AIManager, "chat", classmethod(chat))
  for block in (question, question, copied):
    assert block.id is not None
    async_runner.run(ExtractionBehaviorResolver.extract_local(block.id))
  assert completed[0].extraction_block_id == completed[1].extraction_block_id
  assert not completed[1].created
  assert completed[2].extraction_block_id != completed[0].extraction_block_id
  remove_before_submit = True
  async_runner.run(ExtractionBehaviorResolver.extract_local(answer.id))
  assert read_block(answer.id) is None

  async def delete_result():
    async with graph_uow() as uow:
      await uow.blocks.delete(completed[0].extraction_block_id)

  async_runner.run(delete_result())
  assert read_block(completed[0].extraction_block_id) is None
  assert read_block(question.id) is not None
  assert read_block(copied.id) is not None
  assert read_block(completed[2].extraction_block_id) is not None


def _material(messages):
  return json.loads(messages[1].content[0].text)["initial_material"]


def _proposal(marker, first, second):
  return {
    "text": f"{marker}: Bob suggests packaging issues for Alice; this is advice.",
    "sources": [
      {"observation_id": source["observation_id"], "excerpts": [source["text"]]}
      for source in (first, second)
    ],
  }


def test_parallel_replay_retains_old_evidence_after_source_changes_and_deletion(
  extraction_world, monkeypatch, async_runner
):
  marker, question, answer = extraction_world
  completed = []
  ready = asyncio.Event()
  release = asyncio.Event()
  pending = []

  async def chat(_cls, _model, messages, _tools, _choice):
    material = _material(messages)
    results = [message for message in messages if isinstance(message, ToolResultMessage)]
    if not results:
      return AssistantMessage(
        tool_calls=(
          ToolCall(
            id="read", tool=READ_EXTRACTION_MATERIAL_TOOL, arguments={"block_id": answer.id}
          ),
        )
      )
    assert not results[-1].results[0].is_error
    if len(results) == 1:
      other = results[-1].results[0].content
      proposal = _proposal(marker, material, other)
      pending.append(proposal)
      if len(pending) == 2:
        proposal["sources"].reverse()
        ready.set()
      await release.wait()
      return AssistantMessage(
        tool_calls=(ToolCall(id="submit", tool=CREATE_EXTRACTION_TOOL, arguments=proposal),)
      )
    completed.append(ExtractionWriteResult.model_validate(results[-1].results[0].content))
    return AssistantMessage(content="complete")

  monkeypatch.setattr(AIManager, "chat", classmethod(chat))

  async def run():
    runs = [
      asyncio.create_task(ExtractionBehaviorResolver.extract_local(question.id))
      for _ in range(2)
    ]
    try:
      await asyncio.wait_for(ready.wait(), 5)
      async with graph_uow() as uow:
        await asyncio.wait_for(
          uow.blocks.edit_block(answer.id, content=f"{marker}: New advice."), 3
        )
      release.set()
      await asyncio.wait_for(asyncio.gather(*runs), 10)
    finally:
      for task in runs:
        if not task.done():
          task.cancel()
      await asyncio.gather(*runs, return_exceptions=True)

  async_runner.run(run())
  assert len({result.extraction_block_id for result in completed}) == 1
  assert sorted(result.created for result in completed) == [False, True]
  result_id = completed[0].extraction_block_id
  stored = read_block(result_id)
  assert stored is not None
  assert stored.resolver == "core.text.v1"
  assert f"> {question.content}" in stored.content
  assert f"> {answer.content}" in stored.content
  relations = read_relations(result_id, include_out=False)
  assert {(relation.from_, relation.content) for relation in relations} == {
    (question.id, EXTRACTION_RELATION),
    (answer.id, EXTRACTION_RELATION),
  }
  resolver = ResolverManager.get(stored)
  assert async_runner.run(resolver.get_text()) == stored.content

  async def remove_sources():
    async with graph_uow() as uow:
      await uow.blocks.delete(question.id)
      for relation in await uow.relations.get(result_id, include_out=False):
        assert relation.id is not None
        await uow.relations.delete(relation.id)

  async_runner.run(remove_sources())
  reloaded = read_block(result_id)
  assert reloaded is not None
  fresh = ResolverManager.get(reloaded)
  assert not read_relations(result_id)
  assert async_runner.run(fresh.get_text()) == async_runner.run(resolver.get_text())
  assert "Bob suggests" in async_runner.run(fresh.get_label())
  assert read_block(answer.id) is not None


def test_invalid_excerpts_and_write_failure_leave_no_partial_result(
  extraction_world, monkeypatch, async_runner
):
  marker, question, answer = extraction_world
  failures = []
  invalid_excerpt = True

  async def chat(_cls, _model, messages, _tools, _choice):
    material = _material(messages)
    results = [message for message in messages if isinstance(message, ToolResultMessage)]
    if not results:
      return AssistantMessage(
        tool_calls=(
          ToolCall(
            id="read", tool=READ_EXTRACTION_MATERIAL_TOOL, arguments={"block_id": answer.id}
          ),
        )
      )
    if len(results) == 1:
      assert not results[0].results[0].is_error
      proposal = _proposal(marker, material, results[0].results[0].content)
      if invalid_excerpt:
        proposal["sources"][0]["excerpts"] = ["Alice is an official Python maintainer."]
      return AssistantMessage(
        tool_calls=(ToolCall(id="submit", tool=CREATE_EXTRACTION_TOOL, arguments=proposal),)
      )
    assert results[-1].results[0].is_error
    failures.append(results[-1].results[0].content)
    return AssistantMessage(content="failed")

  monkeypatch.setattr(AIManager, "chat", classmethod(chat))
  async_runner.run(ExtractionBehaviorResolver.extract_local(question.id))
  invalid_excerpt = False
  original_create = RelationRepository.create

  async def failing_create(self, from_, to_, content):
    if from_ == answer.id:
      raise ValueError("injected second-relation failure")
    return await original_create(self, from_, to_, content)

  monkeypatch.setattr(RelationRepository, "create", failing_create)
  async_runner.run(ExtractionBehaviorResolver.extract_local(question.id))
  assert "does not occur" in json.dumps(failures[0])
  assert "injected" in json.dumps(failures[1])
  with TestSession() as db:
    count = (
      db.connection()
      .execute(
        sqlalchemy.text(
          "SELECT count(*) FROM inkcre.blocks "
          "WHERE resolver=:resolver AND content LIKE :marker"
        ),
        {"resolver": "core.text.v1", "marker": f"%{marker}%"},
      )
      .scalar_one()
    )
  assert count == 2  # Only the two input Blocks remain after both rejected writes.
  assert not read_relations(question.id)


def test_cancelled_run_observations_cannot_be_reused(
  extraction_world, monkeypatch, async_runner
):
  marker, question, _ = extraction_world
  entered = asyncio.Event()
  stale = []
  errors = []

  async def waiting_chat(_cls, _model, messages, _tools, _choice):
    stale.append(_material(messages))
    entered.set()
    await asyncio.Event().wait()

  monkeypatch.setattr(AIManager, "chat", classmethod(waiting_chat))

  async def cancel_run():
    task = asyncio.create_task(ExtractionBehaviorResolver.extract_local(question.id))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task

  async_runner.run(cancel_run())

  async def retry_chat(_cls, _model, messages, _tools, _choice):
    if isinstance(messages[-1], ToolResultMessage):
      assert messages[-1].results[0].is_error
      errors.append(messages[-1].results[0].content)
      return AssistantMessage(content="cannot use previous observation")
    return AssistantMessage(
      tool_calls=(
        ToolCall(
          id="stale",
          tool=CREATE_EXTRACTION_TOOL,
          arguments={
            "text": marker,
            "sources": [
              {"observation_id": stale[0]["observation_id"], "excerpts": [question.content]}
            ],
          },
        ),
      )
    )

  monkeypatch.setattr(AIManager, "chat", classmethod(retry_chat))
  async_runner.run(ExtractionBehaviorResolver.extract_local(question.id))
  assert "unavailable" in json.dumps(errors[0])
  with pytest.raises(ValueError, match="active extraction run"):
    async_runner.run(
      ExtractionBehaviorResolver.create_extraction(
        ExtractionProposal(
          text=marker,
          sources=(
            {"observation_id": stale[0]["observation_id"], "excerpts": [question.content]},
          ),
        )
      )
    )


def test_later_read_does_not_replace_the_selected_observation(
  extraction_world, monkeypatch, async_runner
):
  marker, question, _ = extraction_world
  completed: list[ExtractionWriteResult] = []

  async def chat(_cls, _model, messages, _tools, _choice):
    initial = _material(messages)
    results = [message for message in messages if isinstance(message, ToolResultMessage)]
    if not results:
      async with graph_uow() as uow:
        await uow.blocks.edit_block(
          question.id, content=f"{marker}: Repeated sentence. Repeated sentence."
        )
      return AssistantMessage(
        tool_calls=(
          ToolCall(
            id="reread",
            tool=READ_EXTRACTION_MATERIAL_TOOL,
            arguments={"block_id": question.id},
          ),
        )
      )
    later = results[0].results[0].content
    if len(results) == 1:
      assert not results[0].results[0].is_error
      assert isinstance(later, dict)
      assert initial["observation_id"] != later["observation_id"]
      selections = [
        {
          "observation_id": later["observation_id"],
          "excerpts": [later["text"]],
        },
        {"observation_id": initial["observation_id"], "excerpts": [initial["text"]]},
      ]
    elif len(results) == 2:
      assert results[-1].results[0].is_error
      assert "one observation" in json.dumps(results[-1].results[0].content)
      selections = [
        {"observation_id": initial["observation_id"], "excerpts": [initial["text"]]}
      ]
    else:
      assert not results[-1].results[0].is_error
      completed.append(ExtractionWriteResult.model_validate(results[-1].results[0].content))
      return AssistantMessage(content="complete")
    return AssistantMessage(
      tool_calls=(
        ToolCall(
          id="submit",
          tool=CREATE_EXTRACTION_TOOL,
          arguments={
            "text": f"{marker}: Retain the earlier observation.",
            "sources": selections,
          },
        ),
      )
    )

  monkeypatch.setattr(AIManager, "chat", classmethod(chat))
  async_runner.run(ExtractionBehaviorResolver.extract_local(question.id))
  stored = read_block(completed[0].extraction_block_id)
  assert stored is not None
  assert f"> {question.content}" in stored.content
  assert "Repeated sentence" not in stored.content


def test_repeated_and_overlapping_true_excerpts_are_accepted(
  extraction_world, monkeypatch, async_runner
):
  marker, question, _ = extraction_world
  text = f"{marker}: Bob recommends packaging. Bob recommends packaging."
  completed = []

  async def chat(_cls, _model, messages, _tools, _choice):
    if isinstance(messages[-1], ToolResultMessage):
      result = messages[-1].results[0]
      assert not result.is_error
      completed.append(ExtractionWriteResult.model_validate(result.content))
      return AssistantMessage(content="complete")
    material = _material(messages)
    return AssistantMessage(
      tool_calls=(
        ToolCall(
          id="submit",
          tool=CREATE_EXTRACTION_TOOL,
          arguments={
            "text": f"{marker}: Packaging is Bob's suggestion.",
            "sources": [
              {
                "observation_id": material["observation_id"],
                "excerpts": ["Bob recommends packaging.", "recommends packaging."],
              }
            ],
          },
        ),
      )
    )

  async def run():
    async with graph_uow() as uow:
      await uow.blocks.edit_block(question.id, content=text)
    await ExtractionBehaviorResolver.extract_local(question.id)

  monkeypatch.setattr(AIManager, "chat", classmethod(chat))
  async_runner.run(run())
  stored = read_block(completed[0].extraction_block_id)
  assert stored is not None
  assert "> Bob recommends packaging." in stored.content
  assert "> recommends packaging." in stored.content
