"""Core, CLI and first-party Extension release intent and preparation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS_DIRECTORY = PROJECT_ROOT / "extensions"
CHANGELOG_NAME = "CHANGELOG.md"
FRAGMENTS_NAME = ".changes"
FRAGMENT_TYPES = ("added", "changed", "deprecated", "removed", "fixed", "security")
BUMPS = {
  "added": "minor",
  "changed": "patch",
  "deprecated": "minor",
  "removed": "breaking",
  "fixed": "patch",
  "security": "patch",
}
_BUMP_RANK = {"patch": 1, "minor": 2, "major": 3}
_FRAGMENT_PATTERN = re.compile(
  rf"^(?:[+][^.]+|[^+.][^.]*)[.]({'|'.join(FRAGMENT_TYPES)})[.]md$"
)


class ReleaseContractError(RuntimeError):
  """The repository cannot produce an unambiguous project Release."""


@dataclass(frozen=True, order=True)
class SemanticVersion:
  major: int
  minor: int
  patch: int

  @classmethod
  def parse(cls, value: str) -> SemanticVersion:
    match = re.fullmatch(r"(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)", value)
    if match is None:
      raise ReleaseContractError(f"{value!r} is not a canonical stable SemVer")
    return cls(*(int(part) for part in match.groups()))

  def bump(self, kind: str) -> SemanticVersion:
    if kind == "major":
      return SemanticVersion(self.major + 1, 0, 0)
    if kind == "minor":
      return SemanticVersion(self.major, self.minor + 1, 0)
    return SemanticVersion(self.major, self.minor, self.patch + 1)

  def __str__(self) -> str:
    return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class ReleaseProject:
  key: str
  directory: Path
  coordinate: str
  version: SemanticVersion
  extension: bool

  @property
  def changelog(self) -> Path:
    return self.directory / CHANGELOG_NAME

  @property
  def fragments(self) -> Path:
    return self.directory / FRAGMENTS_NAME


def _project_from_pyproject(
  directory: Path, *, extension: bool, key: str | None = None
) -> ReleaseProject:
  value = tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))
  project = value.get("project")
  if not isinstance(project, dict):
    raise ReleaseContractError(f"{directory / 'pyproject.toml'} omits [project]")
  version = project.get("version")
  if not isinstance(version, str):
    raise ReleaseContractError(f"{directory / 'pyproject.toml'} omits project.version")
  if extension:
    producer = value.get("tool", {}).get("inkcre-extension")
    if not isinstance(producer, dict) or not isinstance(producer.get("name"), str):
      raise ReleaseContractError(f"{directory / 'pyproject.toml'} omits Extension identity")
    key = directory.name
    coordinate = producer["name"]
  else:
    if key is None:
      raise ReleaseContractError("a non-Extension project requires an explicit key")
    coordinate = project.get("name", "inkcre-core")
  return ReleaseProject(
    key, directory, coordinate, SemanticVersion.parse(version), extension
  )


def discover_projects(*, extensions_only: bool = False) -> tuple[ReleaseProject, ...]:
  projects: list[ReleaseProject] = []
  if not extensions_only:
    projects.append(_project_from_pyproject(PROJECT_ROOT, extension=False, key="core"))
    projects.append(
      _project_from_pyproject(PROJECT_ROOT / "cli", extension=False, key="cli")
    )
  for directory in sorted(EXTENSIONS_DIRECTORY.iterdir()):
    pyproject = directory / "pyproject.toml"
    if not pyproject.is_file():
      continue
    value = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    if isinstance(value.get("tool", {}).get("inkcre-extension"), dict):
      projects.append(_project_from_pyproject(directory, extension=True))
  if not projects:
    raise ReleaseContractError("no release projects were discovered")
  return tuple(projects)


def project_by_key(key: str) -> ReleaseProject:
  try:
    return {project.key: project for project in discover_projects()}[key]
  except KeyError as error:
    raise ReleaseContractError(f"unknown release project {key!r}") from error


def _run(arguments: list[str], *, cwd: Path = PROJECT_ROOT, check: bool = True):
  result = subprocess.run(  # noqa: S603 -- structured arguments, no shell
    arguments, cwd=cwd, check=False, capture_output=True, text=True
  )
  if check and result.returncode != 0:
    detail = (result.stderr or result.stdout).strip()
    raise ReleaseContractError(f"{' '.join(arguments)} failed: {detail}")
  return result


def _git(*arguments: str, check: bool = True):
  return _run(["git", *arguments], check=check)


def _towncrier(*arguments: str, cwd: Path = PROJECT_ROOT, check: bool = True):
  return _run([sys.executable, "-m", "towncrier", *arguments], cwd=cwd, check=check)


def _fragment_files(project: ReleaseProject) -> tuple[Path, ...]:
  if not project.fragments.is_dir():
    return ()
  return tuple(
    sorted(
      path
      for path in project.fragments.iterdir()
      if path.is_file() and path.name != ".gitkeep"
    )
  )


def validate_fragments(project: ReleaseProject) -> list[str]:
  problems: list[str] = []
  for path in _fragment_files(project):
    match = _FRAGMENT_PATTERN.fullmatch(path.name)
    if match is None:
      problems.append(f"{project.key}: invalid fragment name {path.name!r}")
    if not path.read_text(encoding="utf-8").strip():
      problems.append(f"{project.key}: empty fragment {path.name!r}")
  if not problems and _fragment_files(project):
    result = _towncrier(
      "build",
      "--draft",
      "--config",
      str(PROJECT_ROOT / "towncrier.toml"),
      "--dir",
      str(project.directory),
      "--version",
      str(project.version),
      check=False,
    )
    if result.returncode != 0:
      problems.append(f"{project.key}: {result.stderr.strip() or result.stdout.strip()}")
  return problems


def _show(revision: str, path: Path) -> str | None:
  relative = path.relative_to(PROJECT_ROOT).as_posix()
  result = _git("show", f"{revision}:{relative}", check=False)
  return result.stdout if result.returncode == 0 else None


def _changed_paths(base: str) -> tuple[str, ...]:
  result = _git("diff", "--name-only", f"{base}...HEAD")
  return tuple(line for line in result.stdout.splitlines() if line)


def _production_dependencies_at(revision: str) -> tuple[str, ...] | None:
  content = _show(revision, PROJECT_ROOT / "pyproject.toml")
  if content is None:
    return None
  value = tomllib.loads(content).get("project", {}).get("dependencies")
  return tuple(value) if isinstance(value, list) else None


def _cli_packaging_at(revision: str) -> dict | None:
  content = _show(revision, PROJECT_ROOT / "cli/pyproject.toml")
  if content is None:
    return None
  value = tomllib.loads(content)
  project = value.get("project", {})
  return {
    "project": {key: item for key, item in project.items() if key not in {"version"}},
    "build-system": value.get("build-system"),
    "build": value.get("tool", {}).get("pdm", {}).get("build"),
  }


def affected_projects(base: str) -> set[str]:
  paths = _changed_paths(base)
  affected: set[str] = set()
  extension_keys = {project.key for project in discover_projects(extensions_only=True)}
  core_prefixes = ("inkcre_core/", "app/", "libs/", "migrations/", "utils/")
  core_files = {
    "run.py",
    "Dockerfile",
    "Procfile",
    "alembic.ini",
    ".python-version",
    ".github/workflows/artifact-publish.yml",
    ".github/workflows/production-deploy.yml",
    "scripts/automation/production_delivery.sh",
    "scripts/automation/runtime_artifact.sh",
    "scripts/automation/runtime_contract.sh",
    "scripts/container.py",
    "scripts/web.py",
  }
  for path in paths:
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "extensions" and parts[1] in extension_keys:
      if len(parts) >= 3 and parts[2] == "docs":
        continue
      if FRAGMENTS_NAME not in parts and parts[-1] != CHANGELOG_NAME:
        affected.add(parts[1])
    elif path.startswith(core_prefixes) or path in core_files:
      affected.add("core")
    elif path.startswith("cli/src/") or path in {"cli/README.md", "cli/LICENSE"}:
      affected.add("cli")
  if "pyproject.toml" in paths and (
    _production_dependencies_at(base) != _production_dependencies_at("HEAD")
  ):
    affected.add("core")
  if "cli/pyproject.toml" in paths and _cli_packaging_at(base) != _cli_packaging_at("HEAD"):
    affected.add("cli")
  return affected


def _version_at(project: ReleaseProject, revision: str) -> SemanticVersion | None:
  content = _show(revision, project.directory / "pyproject.toml")
  if content is None:
    return None
  value = tomllib.loads(content).get("project", {}).get("version")
  return SemanticVersion.parse(value) if isinstance(value, str) else None


def _file_changed(path: Path, base: str) -> bool:
  relative = path.relative_to(PROJECT_ROOT).as_posix()
  return (
    _git("diff", "--quiet", f"{base}...HEAD", "--", relative, check=False).returncode == 1
  )


def _fragment_changed(project: ReleaseProject, changed_paths: tuple[str, ...]) -> bool:
  prefix = f"{project.fragments.relative_to(PROJECT_ROOT).as_posix()}/"
  return any(
    path.startswith(prefix) and not path.endswith("/.gitkeep") for path in changed_paths
  )


def check_release_contract(
  base: str | None = None, *, release_pr: bool = False, merged: bool = False
) -> None:
  projects = discover_projects()
  problems = [problem for project in projects for problem in validate_fragments(project)]
  if base is not None:
    affected = affected_projects(base)
    changed_paths = _changed_paths(base)
    if merged:
      release_pr = any(
        _version_at(project, base) not in {None, project.version} for project in projects
      )
    if release_pr:
      allowed = {
        (project.directory / name).relative_to(PROJECT_ROOT).as_posix()
        for project in projects
        for name in ("pyproject.toml", CHANGELOG_NAME)
      }
      fragment_prefixes = tuple(
        project.fragments.relative_to(PROJECT_ROOT).as_posix() + "/" for project in projects
      )
      unexpected = [
        path
        for path in changed_paths
        if path not in allowed and not path.startswith(fragment_prefixes)
      ]
      if unexpected:
        problems.append(
          "Release PR contains non-preparation paths: " + ", ".join(unexpected)
        )
    for project in projects:
      previous = _version_at(project, base)
      version_changed = previous not in {None, project.version}
      changelog_changed = previous is not None and _file_changed(project.changelog, base)
      fragment_changed = _fragment_changed(project, changed_paths)
      if not release_pr and project.key in affected and not fragment_changed:
        problems.append(f"{project.key}: delivered behavior changed without a fragment")
      if not release_pr and (version_changed or changelog_changed):
        problems.append(f"{project.key}: feature changes cannot prepare a release")
      if not release_pr and project.key not in affected and fragment_changed:
        problems.append(f"{project.key}: fragment has no delivered project change")
      if release_pr and version_changed != changelog_changed:
        problems.append(
          f"{project.key}: prepared version and changelog must change together"
        )
  if problems:
    raise ReleaseContractError("\n".join(f"- {problem}" for problem in problems))


def _bump_for(project: ReleaseProject) -> str:
  kinds = []
  for fragment in _fragment_files(project):
    match = _FRAGMENT_PATTERN.fullmatch(fragment.name)
    if match is not None:
      bump = BUMPS[match.group(1)]
      kinds.append(
        "minor"
        if bump == "breaking" and project.version.major == 0
        else "major"
        if bump == "breaking"
        else bump
      )
  if not kinds:
    raise ReleaseContractError(f"{project.key}: no fragments to prepare")
  return max(kinds, key=_BUMP_RANK.__getitem__)


def _replace_version(path: Path, version: SemanticVersion) -> None:
  content = path.read_text(encoding="utf-8")
  updated, count = re.subn(
    r'(?m)^(version = ")[^"]+("\s*)$', rf"\g<1>{version}\g<2>", content, count=1
  )
  if count != 1:
    raise ReleaseContractError(f"could not replace project.version in {path}")
  path.write_text(updated, encoding="utf-8")


def prepare(project_keys: tuple[str, ...] = ()) -> tuple[str, ...]:
  selected = tuple(
    project
    for project in discover_projects()
    if (not project_keys or project.key in project_keys) and _fragment_files(project)
  )
  unknown = set(project_keys) - {project.key for project in discover_projects()}
  if unknown:
    raise ReleaseContractError(f"unknown release projects: {', '.join(sorted(unknown))}")
  problems = [problem for project in selected for problem in validate_fragments(project)]
  if problems:
    raise ReleaseContractError("\n".join(f"- {problem}" for problem in problems))
  if not selected:
    return ()

  with tempfile.TemporaryDirectory(prefix="inkcre-release-") as temporary:
    root = Path(temporary)
    shutil.copy2(PROJECT_ROOT / "towncrier.toml", root / "towncrier.toml")
    prepared: list[tuple[ReleaseProject, SemanticVersion, Path]] = []
    for project in selected:
      relative = project.directory.relative_to(PROJECT_ROOT)
      target = root / relative
      target.mkdir(parents=True, exist_ok=True)
      shutil.copy2(project.directory / "pyproject.toml", target / "pyproject.toml")
      shutil.copy2(project.changelog, target / CHANGELOG_NAME)
      shutil.copytree(project.fragments, target / FRAGMENTS_NAME)
      next_version = project.version.bump(_bump_for(project))
      _towncrier(
        "build",
        "--config",
        str(root / "towncrier.toml"),
        "--dir",
        str(target),
        "--version",
        str(next_version),
        "--yes",
        cwd=root,
      )
      _replace_version(target / "pyproject.toml", next_version)
      prepared.append((project, next_version, target))

    for project, _version, target in prepared:
      shutil.copy2(target / "pyproject.toml", project.directory / "pyproject.toml")
      shutil.copy2(target / CHANGELOG_NAME, project.changelog)
      shutil.rmtree(project.fragments)
      source_fragments = target / FRAGMENTS_NAME
      if source_fragments.exists():
        shutil.copytree(source_fragments, project.fragments)
  return tuple(project.key for project, _version, _target in prepared)


def version_changed(project: ReleaseProject, base: str) -> bool:
  return _version_at(project, base) not in {None, project.version}


def verify_artifact_unchanged(
  project: ReleaseProject, from_revision: str, to_revision: str
) -> None:
  relative = project.directory.relative_to(PROJECT_ROOT).as_posix() or "."
  exclusions = [
    f":(exclude){relative}/{CHANGELOG_NAME}",
    f":(exclude){relative}/{FRAGMENTS_NAME}/**",
  ]
  result = _git(
    "diff", "--quiet", from_revision, to_revision, "--", relative, *exclusions, check=False
  )
  if result.returncode == 1:
    raise ReleaseContractError(
      f"{project.key}: artifact input changed across publication boundary"
    )
  if result.returncode not in {0, 1}:
    raise ReleaseContractError(f"{project.key}: could not compare publication boundary")


def main() -> int:
  parser = argparse.ArgumentParser()
  commands = parser.add_subparsers(dest="command", required=True)
  projects = commands.add_parser("projects")
  projects.add_argument("--json", action="store_true")
  projects.add_argument("--extensions-only", action="store_true")
  check = commands.add_parser("check")
  check.add_argument("--base")
  check.add_argument("--release-pr", action="store_true")
  check.add_argument("--merged", action="store_true")
  prepare_parser = commands.add_parser("prepare")
  prepare_parser.add_argument("projects", nargs="*")
  affected = commands.add_parser("affected")
  affected.add_argument("--base", required=True)
  changed = commands.add_parser("version-changed")
  changed.add_argument("--project", required=True)
  changed.add_argument("--base", required=True)
  unchanged = commands.add_parser("verify-artifact-unchanged")
  unchanged.add_argument("--project", required=True)
  unchanged.add_argument("--from", dest="from_revision", required=True)
  unchanged.add_argument("--to", dest="to_revision", required=True)
  args = parser.parse_args()
  try:
    if args.command == "projects":
      keys = [
        project.key for project in discover_projects(extensions_only=args.extensions_only)
      ]
      print(json.dumps(keys, separators=(",", ":")) if args.json else "\n".join(keys))
    elif args.command == "check":
      check_release_contract(args.base, release_pr=args.release_pr, merged=args.merged)
    elif args.command == "prepare":
      print("\n".join(prepare(tuple(args.projects))))
    elif args.command == "affected":
      print("\n".join(sorted(affected_projects(args.base))))
    elif args.command == "version-changed":
      print(str(version_changed(project_by_key(args.project), args.base)).lower())
    else:
      verify_artifact_unchanged(
        project_by_key(args.project), args.from_revision, args.to_revision
      )
  except ReleaseContractError as error:
    print(error, file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
