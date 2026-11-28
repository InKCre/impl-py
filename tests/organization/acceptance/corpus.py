"""Small loader for ordinary Organization acceptance inputs."""

from pathlib import Path

import pydantic


CORPUS_DIRECTORY = Path(__file__).parent / "corpus"


class Artifact(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  alias: str
  path: str


class InitialRelation(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  from_: str = pydantic.Field(alias="from")
  content: str
  to: str


class InformationWorld(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  id: str
  artifacts: tuple[Artifact, ...]
  relations: tuple[InitialRelation, ...] = ()


class UpstreamChange(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  alias: str
  path: str
  predecessor: str
  relation: str


class CorpusManifest(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  worlds: tuple[InformationWorld, ...]
  upstream_change: UpstreamChange


def load_manifest() -> CorpusManifest:
  manifest = CorpusManifest.model_validate_json(
    (CORPUS_DIRECTORY / "manifest.json").read_text()
  )
  aliases = [artifact.alias for world in manifest.worlds for artifact in world.artifacts]
  if len(aliases) != len(set(aliases)):
    raise ValueError("Organization corpus aliases must be unique")
  known = set(aliases)
  for world in manifest.worlds:
    for artifact in world.artifacts:
      read_artifact(artifact.path)
    for relation in world.relations:
      if relation.from_ not in known or relation.to not in known:
        raise ValueError("Initial relation addresses an unknown corpus alias")
  if manifest.upstream_change.alias in known:
    raise ValueError("Upstream change alias must be new")
  if manifest.upstream_change.predecessor not in known:
    raise ValueError("Upstream change predecessor is unknown")
  read_artifact(manifest.upstream_change.path)
  return manifest


def read_artifact(path: str) -> str:
  candidate = (CORPUS_DIRECTORY / path).resolve()
  if CORPUS_DIRECTORY.resolve() not in candidate.parents:
    raise ValueError("Artifact path escapes the Organization corpus")
  return candidate.read_text().strip()
