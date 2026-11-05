"""Credentialed two-world Organization journey for Human graph review."""

from __future__ import annotations

from inkcre_core.business.deployment_config import DeploymentConfigService
from tests.database import seed_block, seed_relation


import json
import os
import typing
from pathlib import Path

import pytest
import sqlalchemy
import sqlmodel

from inkcre_core.business.ai import AIManager
from inkcre_core.business.graph_navigation_retrieval import GraphNavigationRetrievalManager
from inkcre_core.business.graph_navigation_retrieval.tools import (
  FIND_PATH_TOOL,
  GET_CONNECTED_COMPONENTS_TOOL,
  GET_ENTITY_NEIGHBORHOOD_TOOL,
)
from inkcre_core.business.info_base.tools import GET_ENTITIES_TOOL, RETRIEVE_TOOL
from inkcre_core.business.info_base.resolver import ResolverManager, register_core_resolvers
from inkcre_core.business.info_base.resolver.tools import RESOLVER_TOOL
from inkcre_core.business.job import JobManager
from inkcre_core.business.lexical_retrieval import LexicalRetrievalManager
from inkcre_core.business.organization import (
  ANCHOR_EXISTING_REFERENT_TOOL,
  CREATE_SYNTHESIS_TOOL,
  DRAFT_GRAPH_TOOL,
  GET_DRAFT_GRAPH_SCHEMA_TOOL,
  RECORD_DUPLICATE_ASSERTION_TOOL,
  RECORD_EVIDENCE_STANCE_TOOL,
  RECORD_REFINEMENT_TOOL,
  RECORD_SUPERSESSION_TOOL,
  SUBMIT_GRAPH_TOOL,
  register_core_organization_behaviors,
)
from inkcre_core.business.organization.duplicate_assertion import (
  DUPLICATE_ASSERTION_CONFIG_KEY,
  DUPLICATE_ASSERTION_CONFIG_SCHEMA,
  DUPLICATES_ASSERTION_RELATION,
)
from inkcre_core.business.organization.evidence_stance import (
  EVIDENCE_STANCE_CONFIG_KEY,
  EVIDENCE_STANCE_CONFIG_SCHEMA,
)
from inkcre_core.business.organization.jobs import (
  DUPLICATE_ASSERTION_JOB_TYPE,
  EVIDENCE_STANCE_JOB_TYPE,
  REFERENT_ANCHORING_JOB_TYPE,
  REFINEMENT_JOB_TYPE,
  RUMINATION_JOB_TYPE,
  SUPERSESSION_JOB_TYPE,
  SYNTHESIS_JOB_TYPE,
)
from inkcre_core.business.organization.referent_anchoring import (
  HAS_MENTION_RELATION,
  REFERENT_ANCHORING_CONFIG_KEY,
  REFERENT_ANCHORING_CONFIG_SCHEMA,
  REFERS_TO_RELATION,
)
from inkcre_core.business.organization.refinement import (
  REFINEMENT_CONFIG_KEY,
  REFINEMENT_CONFIG_SCHEMA,
)
from inkcre_core.business.organization.rumination import (
  RUMINATION_CONFIG_KEY,
  RUMINATION_CONFIG_SCHEMA,
)
from inkcre_core.business.organization.supersession import (
  SUPERSESSION_CONFIG_KEY,
  SUPERSESSION_CONFIG_SCHEMA,
  SUPERSESSION_BEHAVIOR,
  SUPERSEDES_RELATION,
)
from inkcre_core.business.organization.synthesis import (
  SYNTHESIS_CONFIG_KEY,
  SYNTHESIS_CONFIG_SCHEMA,
  SYNTHESIS_RELATION,
)
from tests.database import TestSession
from inkcre_core.schemas import AgentDefinitionModel
from inkcre_core.schemas.ai import AIModelModel, AIProviderModel, ChatCapability
from inkcre_core.schemas.deployment_config import (
  DeploymentConfigModel,
  DeploymentConfigView,
)
from inkcre_core.schemas.info_base.block import BlockForm, BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.job import JobModel, JobStatus
from inkcre_core.schemas.lexical_retrieval import LexicalMaintenanceOptions

from .corpus import CorpusManifest, load_manifest, read_artifact


pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

_REQUIRED_ENVIRONMENT = (
  "INKCRE_TEST_DATABASE_URL",
  "INKCRE_ORGANIZATION_ACCEPTANCE_AI_API_KEY",
  "INKCRE_ORGANIZATION_ACCEPTANCE_CHAT_MODEL",
)
_READ_TOOLS = (
  RETRIEVE_TOOL,
  RESOLVER_TOOL,
  GET_ENTITIES_TOOL,
  GET_ENTITY_NEIGHBORHOOD_TOOL,
  FIND_PATH_TOOL,
  GET_CONNECTED_COMPONENTS_TOOL,
)


class _Behavior(typing.NamedTuple):
  name: str
  config_key: str
  config_schema: str
  job_type: str
  mutation_tools: tuple[str, ...]


_BEHAVIORS = (
  _Behavior(
    "rumination",
    RUMINATION_CONFIG_KEY,
    RUMINATION_CONFIG_SCHEMA,
    RUMINATION_JOB_TYPE,
    (GET_DRAFT_GRAPH_SCHEMA_TOOL, DRAFT_GRAPH_TOOL, SUBMIT_GRAPH_TOOL),
  ),
  _Behavior(
    "supersession",
    SUPERSESSION_CONFIG_KEY,
    SUPERSESSION_CONFIG_SCHEMA,
    SUPERSESSION_JOB_TYPE,
    (RECORD_SUPERSESSION_TOOL,),
  ),
  _Behavior(
    "refinement",
    REFINEMENT_CONFIG_KEY,
    REFINEMENT_CONFIG_SCHEMA,
    REFINEMENT_JOB_TYPE,
    (RECORD_REFINEMENT_TOOL,),
  ),
  _Behavior(
    "evidence stance",
    EVIDENCE_STANCE_CONFIG_KEY,
    EVIDENCE_STANCE_CONFIG_SCHEMA,
    EVIDENCE_STANCE_JOB_TYPE,
    (RECORD_EVIDENCE_STANCE_TOOL,),
  ),
  _Behavior(
    "synthesis",
    SYNTHESIS_CONFIG_KEY,
    SYNTHESIS_CONFIG_SCHEMA,
    SYNTHESIS_JOB_TYPE,
    (CREATE_SYNTHESIS_TOOL,),
  ),
  _Behavior(
    "existing referent anchoring",
    REFERENT_ANCHORING_CONFIG_KEY,
    REFERENT_ANCHORING_CONFIG_SCHEMA,
    REFERENT_ANCHORING_JOB_TYPE,
    (ANCHOR_EXISTING_REFERENT_TOOL,),
  ),
  _Behavior(
    "duplicate assertion",
    DUPLICATE_ASSERTION_CONFIG_KEY,
    DUPLICATE_ASSERTION_CONFIG_SCHEMA,
    DUPLICATE_ASSERTION_JOB_TYPE,
    (RECORD_DUPLICATE_ASSERTION_TOOL,),
  ),
)


def _available() -> bool:
  return all(os.getenv(name) for name in _REQUIRED_ENVIRONMENT)


def _required_id(value: int | None) -> int:
  assert value is not None
  return value


def _ingest(manifest: CorpusManifest) -> dict[str, int]:
  aliases: dict[str, int] = {}
  with TestSession() as db_session:
    for world in manifest.worlds:
      for artifact in world.artifacts:
        block = seed_block(
          BlockForm(resolver="core.text.v1", content=read_artifact(artifact.path)),
          db_session,
        )
        aliases[artifact.alias] = _required_id(block.id)
      for relation in world.relations:
        seed_relation(
          aliases[relation.from_],
          aliases[relation.to],
          relation.content,
          db_session,
        )
    db_session.commit()
  return aliases


async def _maintain_lexical_projection() -> None:
  report = await LexicalRetrievalManager.maintain(
    LexicalMaintenanceOptions(max_records=10_000, scan_page_size=100)
  )
  assert report.failed == 0, report.diagnostics


async def _create_provider_and_model() -> tuple[int, int]:
  await AIManager.sync_dialects_async()
  config = {"api_key": os.environ["INKCRE_ORGANIZATION_ACCEPTANCE_AI_API_KEY"]}
  if base_url := os.getenv("INKCRE_ORGANIZATION_ACCEPTANCE_AI_BASE_URL"):
    config["base_url"] = base_url
  with TestSession() as db_session:
    provider = AIProviderModel(
      name="Organization acceptance provider",
      dialect="core.openai-compatible.v1",
      config=config,
    )
    db_session.add(provider)
    db_session.flush()
    model = AIModelModel(
      provider=_required_id(provider.id),
      native_model_id=os.environ["INKCRE_ORGANIZATION_ACCEPTANCE_CHAT_MODEL"],
      capabilities=(
        ChatCapability(
          input_modalities=["text"],
          output_modalities=["text"],
          features=["tool_calling"],
        ),
      ),
    )
    db_session.add(model)
    db_session.commit()
    return _required_id(provider.id), _required_id(model.id)


def _create_agents(model_id: int) -> dict[str, int]:
  definitions = json.loads(Path(__file__).with_name("agent_definitions.json").read_text())
  result: dict[str, int] = {}
  with TestSession() as db_session:
    for behavior in _BEHAVIORS:
      definition = definitions["agents"][behavior.name]
      agent = AgentDefinitionModel(
        name=f"Organization acceptance: {behavior.name}",
        system_prompt=(
          definition["system_prompt"]
          if behavior.name == "rumination"
          else definitions["common_system_prompt"] + "\n\n" + definition["system_prompt"]
        ),
        tools=tuple(definition["tools"]),
        tool_choice="auto",
        model=model_id,
        max_model_calls_per_turn=12,
      )
      db_session.add(agent)
      db_session.flush()
      result[behavior.name] = _required_id(agent.id)
    db_session.commit()
  return result


async def _configure_agents(agent_ids: dict[str, int]) -> None:
  for behavior in _BEHAVIORS:
    await DeploymentConfigService.replace(
      behavior.config_key, behavior.config_schema, {"agent": agent_ids[behavior.name]}
    )


async def _run_round(round_number: int) -> list[dict[str, typing.Any]]:
  results: list[dict[str, typing.Any]] = []
  for behavior in _BEHAVIORS:
    job = await JobManager.create(behavior.job_type, {"max_seeds": 100})
    job_id = _required_id(job.id)
    claimed = await JobManager.run(job_id)
    with TestSession() as db_session:
      closed = db_session.get(JobModel, job_id)
    assert claimed and closed is not None
    assert closed.status is JobStatus.FINISHED, closed.state
    results.append(
      {
        "round": round_number,
        "behavior": behavior.name,
        "job": job_id,
        "status": closed.status.value,
        "state": closed.state,
      }
    )
  return results


def _apply_upstream_change(
  manifest: CorpusManifest,
  aliases: dict[str, int],
) -> None:
  change = manifest.upstream_change
  with TestSession() as db_session:
    block = seed_block(
      BlockForm(resolver="core.text.v1", content=read_artifact(change.path)),
      db_session,
    )
    block_id = _required_id(block.id)
    seed_relation(
      aliases[change.predecessor],
      block_id,
      change.relation,
      db_session,
    )
    db_session.commit()
  aliases[change.alias] = block_id


def _snapshot_graph(block_ids_before: set[int]) -> dict[str, typing.Any]:
  with TestSession() as db_session:
    blocks = db_session.exec(sqlmodel.select(BlockModel)).all()
    relations = db_session.exec(sqlmodel.select(RelationModel)).all()
  new_blocks = [block for block in blocks if _required_id(block.id) not in block_ids_before]
  visible_ids = {_required_id(block.id) for block in new_blocks}
  visible_relations = [
    relation
    for relation in relations
    if relation.from_ in visible_ids or relation.to_ in visible_ids
  ]
  return {
    "blocks": [
      {"id": block.id, "resolver": block.resolver, "content": block.content}
      for block in new_blocks
    ],
    "relations": [
      {
        "id": relation.id,
        "from": relation.from_,
        "content": relation.content,
        "to": relation.to_,
      }
      for relation in visible_relations
    ],
  }


async def _use_readback() -> dict[str, typing.Any]:
  with TestSession() as db_session:
    relations = db_session.exec(sqlmodel.select(RelationModel)).all()
    duplicate_edges = [
      relation
      for relation in relations
      if relation.content == DUPLICATES_ASSERTION_RELATION
    ]
    supersession_edges = [
      relation for relation in relations if relation.content == SUPERSEDES_RELATION
    ]
    synthesis_edges = [
      relation for relation in relations if relation.content == SYNTHESIS_RELATION
    ]
    mention_edges = [
      relation
      for relation in relations
      if relation.content in {HAS_MENTION_RELATION, REFERS_TO_RELATION}
    ]
    descriptor = db_session.exec(
      sqlmodel.select(BlockModel).where(BlockModel.resolver == SUPERSESSION_BEHAVIOR)
    ).first()

  duplicate_components = None
  if duplicate_edges:
    duplicate_seeds = tuple(
      dict.fromkeys(
        endpoint
        for relation in duplicate_edges
        for endpoint in (relation.from_, relation.to_)
      )
    )
    duplicate_components = (
      await GraphNavigationRetrievalManager.get_connected_components(
        duplicate_seeds,
        contents=(DUPLICATES_ASSERTION_RELATION,),
      )
    ).model_dump(mode="json")

  lineage = None
  if descriptor is not None and supersession_edges:
    lineage = await ResolverManager.invoke_method(
      descriptor,
      "read_lineage",
      {"focal_block_id": supersession_edges[0].from_},
    )
    lineage = lineage.model_dump(mode="json")

  return {
    "duplicate_components": duplicate_components,
    "supersession_lineage": lineage,
    "synthesis_basis": [relation.model_dump(mode="json") for relation in synthesis_edges],
    "referent_paths": [relation.model_dump(mode="json") for relation in mention_edges],
  }


async def _restore_config(key: str, backup: DeploymentConfigView | None) -> None:
  with TestSession() as db_session:
    record = db_session.get(DeploymentConfigModel, key)
    if record is not None:
      db_session.delete(record)
      db_session.commit()
  if backup is not None:
    await DeploymentConfigService.replace(key, backup.schema_id, backup.value)


async def _cleanup(  # noqa: PLR0913
  *,
  block_ids_before: set[int],
  job_ids_before: set[int],
  config_backups: dict[str, DeploymentConfigView | None],
  agent_ids: typing.Collection[int],
  model_id: int | None,
  provider_id: int | None,
) -> None:
  for key, backup in config_backups.items():
    await _restore_config(key, backup)
  with TestSession() as db_session:
    new_block_ids = (
      set(db_session.exec(sqlmodel.select(BlockModel.id)).all()) - block_ids_before
    )
    if new_block_ids:
      db_session.connection().execute(
        sqlalchemy.text(
          "DELETE FROM inkcre.relations WHERE from_ = ANY(:ids) OR to_ = ANY(:ids)"
        ),
        {"ids": list(new_block_ids)},
      )
      db_session.connection().execute(
        sqlalchemy.text("DELETE FROM inkcre.blocks WHERE id = ANY(:ids)"),
        {"ids": list(new_block_ids)},
      )
    new_job_ids = set(db_session.exec(sqlmodel.select(JobModel.id)).all()) - job_ids_before
    for job_id in new_job_ids:
      job = db_session.get(JobModel, job_id)
      if job is not None:
        db_session.delete(job)
    for agent_id in agent_ids:
      agent = db_session.get(AgentDefinitionModel, agent_id)
      if agent is not None:
        db_session.delete(agent)
    if model_id is not None:
      model = db_session.get(AIModelModel, model_id)
      if model is not None:
        db_session.delete(model)
    if provider_id is not None:
      provider = db_session.get(AIProviderModel, provider_id)
      if provider is not None:
        db_session.delete(provider)
    db_session.commit()


@pytest.mark.skipif(
  not _available(),
  reason=(
    "requires a migrated PostgreSQL database and Organization acceptance chat provider"
  ),
)
def test_two_information_worlds_are_organized_for_human_review(
  async_runner,
) -> None:
  manifest = load_manifest()
  register_core_resolvers()
  register_core_organization_behaviors()
  async_runner.run(JobManager.sync_job_types())
  with TestSession() as db_session:
    block_ids_before = {
      _required_id(block_id)
      for block_id in db_session.exec(sqlmodel.select(BlockModel.id)).all()
    }
    job_ids_before = {
      _required_id(job_id) for job_id in db_session.exec(sqlmodel.select(JobModel.id)).all()
    }

  provider_id: int | None = None
  model_id: int | None = None
  agent_ids: dict[str, int] = {}
  config_backups: dict[str, DeploymentConfigView | None] = {}
  aliases: dict[str, int] = {}
  job_results: list[dict[str, typing.Any]] = []
  try:
    aliases = _ingest(manifest)
    async_runner.run(_maintain_lexical_projection())
    provider_id, model_id = async_runner.run(_create_provider_and_model())
    agent_ids = _create_agents(model_id)
    config_backups = {
      behavior.config_key: async_runner.run(
        DeploymentConfigService.read(behavior.config_key)
      )
      for behavior in _BEHAVIORS
    }
    async_runner.run(_configure_agents(agent_ids))
    job_results.extend(async_runner.run(_run_round(1)))
    _apply_upstream_change(manifest, aliases)
    async_runner.run(_maintain_lexical_projection())
    job_results.extend(async_runner.run(_run_round(2)))

    evidence = {
      "aliases": aliases,
      "jobs": job_results,
      "graph": _snapshot_graph(block_ids_before),
      "later_use": async_runner.run(_use_readback()),
    }
    print("ORGANIZATION_ACCEPTANCE_EVIDENCE=" + json.dumps(evidence, ensure_ascii=False))
  finally:
    async_runner.run(
      _cleanup(
        block_ids_before=block_ids_before,
        job_ids_before=job_ids_before,
        config_backups=config_backups,
        agent_ids=agent_ids.values(),
        model_id=model_id,
        provider_id=provider_id,
      )
    )
