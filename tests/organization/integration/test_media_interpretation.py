"""Real-storage vertical proof for media textualization and interpretation Jobs."""

from inkcre_core.business.deployment_config import DeploymentConfigService
from inkcre_core.persistence.info_base.uow import graph_uow
from tests.database import read_relations


import json
import os
from pathlib import Path
import typing

import pytest
import sqlalchemy

import inkcre_core.business.organization_job  # noqa: F401  # exact Job registration
from inkcre_core.business.ai import AIManager
from inkcre_core.business.cron import CronManager
from inkcre_core.business.info_base.resolver import register_core_resolvers
from inkcre_core.business.info_base.resolver.audio import (
  AUDIO_RESOLVER_CONFIG_KEY,
  AUDIO_RESOLVER_CONFIG_SCHEMA,
)
from inkcre_core.business.info_base.resolver.image import (
  IMAGE_RESOLVER_CONFIG_KEY,
  IMAGE_RESOLVER_CONFIG_SCHEMA,
)
from inkcre_core.business.info_base.resolver.video import (
  VIDEO_RESOLVER_CONFIG_KEY,
  VIDEO_RESOLVER_CONFIG_SCHEMA,
)
from inkcre_core.business.info_base.storage import StorageManager, WritableStorage
from inkcre_core.business.job import JobManager
from inkcre_core.business.lexical_retrieval import LexicalRetrievalManager
from inkcre_core.business.organization import SUBMIT_GRAPH_TOOL
from inkcre_core.business.organization_media import (
  MEDIA_INTERPRETATION_CONFIG_KEY,
  MEDIA_INTERPRETATION_CONFIG_SCHEMA,
  MEDIA_INTERPRETATION_JOB_TYPE,
)
from tests.database import TestSession
from inkcre_core.schemas import AgentDefinitionModel
from inkcre_core.schemas.ai import (
  AIModelModel,
  AIProviderModel,
  AssistantMessage,
  AudioContentPart,
  ChatCapability,
  ImageContentPart,
  TextContentPart,
  ToolCall,
  ToolResultMessage,
  UserMessage,
  VideoContentPart,
)
from inkcre_core.schemas.cron import CronModel
from inkcre_core.schemas.deployment_config import DeploymentConfigModel
from inkcre_core.schemas.info_base.block import BlockForm, BlockModel
from inkcre_core.schemas.job import JobModel, JobStatus
from inkcre_core.schemas.lexical_retrieval import LexicalMaintenanceOptions


pytestmark = pytest.mark.skipif(
  not os.getenv("INKCRE_TEST_DATABASE_URL"),
  reason="requires an explicitly selected migrated PostgreSQL runtime",
)

ASSETS = Path(__file__).parents[2] / "assets" / "semantic-content"
CONFIG_KEYS = (
  IMAGE_RESOLVER_CONFIG_KEY,
  AUDIO_RESOLVER_CONFIG_KEY,
  VIDEO_RESOLVER_CONFIG_KEY,
  MEDIA_INTERPRETATION_CONFIG_KEY,
)


@pytest.fixture
def job_execution(async_runner):
  JobManager.start()
  try:
    yield
  finally:
    async_runner.run(JobManager.shutdown())


def _reset_runtime_rows() -> None:
  with TestSession() as db:
    db.connection().execute(
      sqlalchemy.text(
        "TRUNCATE TABLE inkcre.block_lexical_records, inkcre.jobs, inkcre.crons, "
        "inkcre.relations, inkcre.blocks, inkcre.storage_blobs CASCADE"
      )
    )
    db.commit()


def _backup_configs() -> dict[str, dict[str, typing.Any]]:
  with TestSession() as db:
    return {
      key: record.model_dump()
      for key in CONFIG_KEYS
      if (record := db.get(DeploymentConfigModel, key)) is not None
    }


def _restore_configs(backup: dict[str, dict[str, typing.Any]]) -> None:
  with TestSession() as db:
    for key in CONFIG_KEYS:
      current = db.get(DeploymentConfigModel, key)
      if current is not None:
        db.delete(current)
    db.flush()
    for record in backup.values():
      db.add(DeploymentConfigModel.model_validate(record))
    db.commit()


async def _stored_media(resolver: str, content: bytes) -> BlockModel:
  async with graph_uow() as uow:
    storage = StorageManager.from_record(await uow.storage.get(-4))
    assert isinstance(storage, WritableStorage)
    pointer = await storage.create_content(content, uow.storage)
    return await uow.blocks.create(
      BlockForm(storage=-4, resolver=resolver, content=pointer)
    )


def _related(block: int, role: str) -> tuple[BlockModel, ...]:
  relations = read_relations(
    block,
    include_in=False,
    include_out=True,
    content=role,
  )
  with TestSession() as db:
    return tuple(
      target
      for relation in relations
      if (target := db.get(BlockModel, relation.to_)) is not None
    )


def test_media_textualization_interpretation_and_lexical_recall(
  async_runner,
  job_execution,
  monkeypatch: pytest.MonkeyPatch,
  semantic_content_assets: Path,
) -> None:
  assert semantic_content_assets == ASSETS
  _reset_runtime_rows()
  register_core_resolvers()
  async_runner.run(StorageManager.setup_builtin_storages_async())
  async_runner.run(AIManager.sync_dialects_async())
  backup = _backup_configs()
  provider_id: int | None = None
  model_id: int | None = None
  agent_id: int | None = None
  faithful_calls: list[tuple[int, str]] = []
  agent_modalities: list[str] = []

  try:
    with TestSession() as db:
      provider = AIProviderModel(
        name="Media acceptance provider",
        dialect="core.alibaba-model-studio.v1",
        config={"api_key": "unused"},
      )
      db.add(provider)
      db.flush()
      provider_id = provider.id
      assert provider_id is not None

      model = AIModelModel(
        provider=provider_id,
        native_model_id="media-acceptance-model",
        capabilities=(
          ChatCapability(
            input_modalities=["text", "image", "audio", "video"],
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
        name="Media interpretation acceptance Agent",
        system_prompt="Submit one concise interpretation connected to the focal Block.",
        tools=(SUBMIT_GRAPH_TOOL,),
        tool_choice="auto",
        model=model_id,
        max_model_calls_per_turn=2,
      )
      db.add(agent)
      db.commit()
      db.refresh(agent)
      agent_id = agent.id
      assert agent_id is not None

    async_runner.run(
      DeploymentConfigService.replace(
        IMAGE_RESOLVER_CONFIG_KEY,
        IMAGE_RESOLVER_CONFIG_SCHEMA,
        {"text_model": model_id},
      )
    )
    async_runner.run(
      DeploymentConfigService.replace(
        AUDIO_RESOLVER_CONFIG_KEY,
        AUDIO_RESOLVER_CONFIG_SCHEMA,
        {"transcript_model": model_id},
      )
    )
    async_runner.run(
      DeploymentConfigService.replace(
        VIDEO_RESOLVER_CONFIG_KEY,
        VIDEO_RESOLVER_CONFIG_SCHEMA,
        {"text_model": model_id, "transcript_model": model_id},
      )
    )
    async_runner.run(
      DeploymentConfigService.replace(
        MEDIA_INTERPRETATION_CONFIG_KEY,
        MEDIA_INTERPRETATION_CONFIG_SCHEMA,
        {
          "image_agent": agent_id,
          "audio_agent": 2**62,
          "video_agent": agent_id,
        },
      )
    )

    image = async_runner.run(
      _stored_media("core.image.v1", (ASSETS / "image.png").read_bytes())
    )
    audio = async_runner.run(
      _stored_media("core.audio.v1", (ASSETS / "audio.wav").read_bytes())
    )
    video = async_runner.run(
      _stored_media(
        "core.video.v1",
        (ASSETS / "video-subtitled.mkv").read_bytes(),
      )
    )
    pdf = async_runner.run(
      _stored_media("core.pdf.v1", (ASSETS / "document.pdf").read_bytes())
    )
    assert image.id is not None and audio.id is not None and video.id is not None

    async def chat(_cls, model, messages, tools=(), tool_choice=None):
      assert model == model_id
      if not tools:
        instruction = messages[0].content
        media = messages[-1].content[0]
        faithful_calls.append((model, instruction))
        if isinstance(media, ImageContentPart):
          return AssistantMessage(content="optical flight software checklist")
        if isinstance(media, AudioContentPart):
          return AssistantMessage(content="spoken verification gate")
        assert isinstance(media, VideoContentPart)
        return AssistantMessage(
          content=(
            "simulation interface rehearsal"
            if "speech" in instruction
            else "integration status display"
          )
        )

      assert tool_choice == "auto"
      if isinstance(messages[-1], UserMessage):
        text_part = messages[-1].content[0]
        assert isinstance(text_part, TextContentPart)
        context = json.loads(text_part.text)
        focal = context["focal_block"]
        media = messages[-1].content[1]
        modality = media.type
        agent_modalities.append(modality)
        return AssistantMessage(
          tool_calls=(
            ToolCall(
              id=f"interpret-{focal['id']}",
              tool=SUBMIT_GRAPH_TOOL,
              arguments={
                "graph": {
                  "blocks": [
                    {
                      "id": -1,
                      "resolver": "core.text.v1",
                      "content": (
                        f"orbital integration strategy interpreted from {modality}"
                      ),
                    }
                  ],
                  "relations": [
                    {
                      "from_": focal["id"],
                      "to_": -1,
                      "content": "interpretation",
                    }
                  ],
                }
              },
            ),
          )
        )
      assert isinstance(messages[-1], ToolResultMessage)
      assert not messages[-1].results[0].is_error
      return AssistantMessage(content="complete")

    monkeypatch.setattr(AIManager, "chat", classmethod(chat))

    first_maintenance = async_runner.run(
      LexicalRetrievalManager.maintain(
        LexicalMaintenanceOptions(max_records=30, scan_page_size=3)
      )
    )
    assert first_maintenance.failed == first_maintenance.unavailable == 0
    expected_roles = {
      image.id: ("text",),
      audio.id: ("transcript",),
      video.id: ("subtitle", "transcript", "text"),
    }
    for parent, roles in expected_roles.items():
      for role in roles:
        assert len(_related(parent, role)) == 1

    clues = {
      image.id: "optical flight software checklist",
      audio.id: "spoken verification gate",
      video.id: "Flight software integration rehearsal",
    }
    for parent, clue in clues.items():
      result = async_runner.run(LexicalRetrievalManager.retrieve_local(clue))
      assert result.matches
      assert result.matches[0].block.id in {
        child.id for role in expected_roles[parent] for child in _related(parent, role)
      }
    pdf_body = async_runner.run(
      LexicalRetrievalManager.retrieve_local("authoritative write-ahead log")
    )
    assert pdf_body.matches[0].block.id == pdf.id

    faithful_call_count = len(faithful_calls)
    rebuild = async_runner.run(
      LexicalRetrievalManager.rebuild(
        LexicalMaintenanceOptions(max_records=30, scan_page_size=3)
      )
    )
    assert rebuild.failed == rebuild.unavailable == 0
    assert len(faithful_calls) == faithful_call_count
    for parent, roles in expected_roles.items():
      for role in roles:
        assert len(_related(parent, role)) == 1

    async_runner.run(JobManager.sync_job_types())
    with TestSession() as db:
      cron = CronModel(
        schedule="* * * * *",
        job_type=MEDIA_INTERPRETATION_JOB_TYPE,
        job_parameters={},
      )
      db.add(cron)
      db.commit()
      db.refresh(cron)
      assert cron.id is not None
      cron_id = cron.id

    job = async_runner.run(CronManager.run_now(cron_id))
    assert job.id is not None
    assert async_runner.run(JobManager.run(job.id))
    with TestSession() as db:
      persisted = db.get(JobModel, job.id)
      assert persisted is not None
      assert persisted.status == JobStatus.FINISHED
      assert persisted.state == {
        "selected": 3,
        "interpreted": 2,
        "unavailable": 1,
        "failed": 0,
        "no_output": 0,
        "diagnostics": [
          {
            "block": audio.id,
            "modality": "audio",
            "outcome": "unavailable",
            "reason": "agent_not_locally_executable",
          }
        ],
      }
    assert agent_modalities == ["image", "video"]
    assert len(_related(image.id, "interpretation")) == 1
    assert not _related(audio.id, "interpretation")
    assert len(_related(video.id, "interpretation")) == 1

    interpretation_maintenance = async_runner.run(LexicalRetrievalManager.maintain())
    assert interpretation_maintenance.failed == 0
    interpretation = async_runner.run(
      LexicalRetrievalManager.retrieve_local("orbital integration strategy")
    )
    assert len(interpretation.matches) == 2
    assert {match.block.id for match in interpretation.matches} == {
      _related(image.id, "interpretation")[0].id,
      _related(video.id, "interpretation")[0].id,
    }

    second = async_runner.run(JobManager.create(MEDIA_INTERPRETATION_JOB_TYPE, {}))
    assert second.id is not None
    assert async_runner.run(JobManager.run(second.id))
    with TestSession() as db:
      persisted = db.get(JobModel, second.id)
      assert persisted is not None
      assert persisted.state["selected"] == 1
      assert persisted.state["unavailable"] == 1
  finally:
    _restore_configs(backup)
    with TestSession() as db:
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
    _reset_runtime_rows()
