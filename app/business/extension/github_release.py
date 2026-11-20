"""Resolve and stage exact Python wheels published as GitHub Releases."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote, unquote, urlsplit
from zipfile import BadZipFile, ZipFile

import httpx
from inkcre_extension_runtime_core_py.contracts import InstalledExtensionRecord
from packaging.tags import sys_tags
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from pydantic import BaseModel, TypeAdapter
from typing import Literal

from inkcre_core.schemas.extension import ExtensionSourceRepository
from app.settings import settings

from .distribution import AcquiredDistribution
from .errors import ExtensionAcquisitionError
from .release import ExtensionReleaseDescriptor, require_python_association


class _ReleaseManifest(BaseModel):
  schema_version: Literal[1]
  source_path: str
  host_sdk: str
  host_sdk_version: str


class _Asset(BaseModel):
  name: str
  browser_download_url: str
  digest: str | None = None


class _Release(BaseModel):
  tag_name: str
  body: str
  draft: bool
  assets: list[_Asset]


_REPOSITORY = TypeAdapter(ExtensionSourceRepository)
_MANIFEST = re.compile(r"<!-- inkcre-release\r?\n(.*?)\r?\n-->", re.DOTALL)
_ARCHIVE_DIRECTORY = "inkcre-github-release"


def release_from_record(
  record: InstalledExtensionRecord,
) -> ExtensionReleaseDescriptor:
  """Project native wheel metadata into the existing Runtime apply interface."""
  return ExtensionReleaseDescriptor.model_validate(
    {
      "name": record.name,
      "nickname": record.name.rsplit("/", 1)[-1],
      "version": record.version,
      "distribution": "python",
      "state": "published",
      "python": {
        "project": record.python.project,
        "host_sdk": record.host_sdk.name,
        "host_sdk_version": record.host_sdk.version,
        "entry_point": record.python.entry_point.model_dump(),
        "simple_url": "",
      },
    }
  )


def _select_wheel(assets: list[_Asset]) -> _Asset:
  ranks = {tag: index for index, tag in enumerate(sys_tags())}
  choices: list[tuple[int, str, _Asset]] = []
  for asset in assets:
    if not asset.name.endswith(".whl"):
      continue
    try:
      _, _, _, tags = parse_wheel_filename(asset.name)
    except InvalidWheelFilename:
      continue
    supported = [ranks[tag] for tag in tags if tag in ranks]
    if supported:
      choices.append((min(supported), asset.name, asset))
  if not choices:
    raise ExtensionAcquisitionError("GitHub Release has no compatible Python wheel")
  return min(choices, key=lambda choice: choice[:2])[2]


def _repository_coordinate(repository: str) -> tuple[str, str]:
  repository = _REPOSITORY.validate_python(repository)
  _, owner, name = urlsplit(repository).path.split("/")
  if name in {".", ".."}:
    raise ValueError("repository name is invalid")
  return owner, name


def _installed_archive(installed: AcquiredDistribution) -> tuple[str, str] | None:
  raw = installed.distribution.read_text("direct_url.json")
  if raw is None:
    return None
  try:
    metadata = json.loads(raw)
    url = metadata.get("url")
    digest = metadata.get("archive_info", {}).get("hashes", {}).get("sha256")
    if (
      isinstance(url, str)
      and isinstance(digest, str)
      and re.fullmatch("[0-9a-f]{64}", digest)
    ):
      return url, digest
  except (AttributeError, ValueError):
    pass
  return None


def installed_from_repository(installed: AcquiredDistribution, repository: str) -> bool:
  """Match the repository in the staged file URL recorded by pip."""
  archive = _installed_archive(installed)
  if archive is None:
    return False
  try:
    owner, name = _repository_coordinate(repository)
    url = urlsplit(archive[0])
  except ValueError:
    return False
  parts = PurePosixPath(unquote(url.path)).parts
  return (url.scheme, url.netloc) == ("file", "") and parts[-4:-1] == (
    _ARCHIVE_DIRECTORY,
    owner,
    name,
  )


def _read_wheel(path: Path) -> InstalledExtensionRecord:
  with ZipFile(path) as wheel:
    records = [
      filename
      for filename in wheel.namelist()
      if filename.endswith(".dist-info/inkcre-extension.json") and filename.count("/") == 1
    ]
    if len(records) != 1:
      raise ExtensionAcquisitionError("Wheel must contain one installed Extension record")
    return InstalledExtensionRecord.model_validate_json(wheel.read(records[0]))


def stage_github_distribution(
  repository: str,
  name: str,
  version: str,
  wheel_dir: Path | None,
  *,
  installed: AcquiredDistribution | None,
) -> tuple[ExtensionReleaseDescriptor, Path | None]:
  """Read the selected Release and stage its wheel, or reuse an identical install."""
  try:
    owner, repo = _repository_coordinate(repository)
    slug = f"{owner}/{repo}"
    tag = f"inkcre-ext/{name}/python/v{version}"
    with httpx.Client(
      timeout=settings.extension_registry_timeout_seconds, follow_redirects=True
    ) as client:
      response = client.get(
        f"https://api.github.com/repos/{slug}/releases/tags/{quote(tag, safe='')}",
        headers={"Accept": "application/vnd.github+json"},
      )
      release = _Release.model_validate(response.raise_for_status().json())
      if release.tag_name != tag or release.draft:
        raise ExtensionAcquisitionError("GitHub did not return the exact published Release")
      match = _MANIFEST.search(release.body)
      if match is None:
        raise ExtensionAcquisitionError(
          "GitHub Release is missing its inkcre-release description"
        )
      manifest = _ReleaseManifest.model_validate_json(match[1])
      asset = _select_wheel(release.assets)
      algorithm, _, digest = (asset.digest or "").partition(":")
      if algorithm != "sha256" or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ExtensionAcquisitionError(
          "Python wheel attachment has no GitHub SHA-256 digest"
        )

      path = None
      if installed is not None:
        archive = _installed_archive(installed)
        if archive is None or archive[1] != digest:
          raise ExtensionAcquisitionError(
            "The installed wheel differs from the selected Release attachment"
          )
        record = installed.record
      else:
        assert wheel_dir is not None
        asset_url = urlsplit(asset.browser_download_url)
        prefix = f"/{slug}/releases/download/".lower()
        if (asset_url.scheme, asset_url.netloc) != (
          "https",
          "github.com",
        ) or not asset_url.path.lower().startswith(prefix):
          raise ExtensionAcquisitionError(
            "Python wheel attachment is outside the publisher Release"
          )
        path = wheel_dir / _ARCHIVE_DIRECTORY / owner / repo / asset.name
        path.parent.mkdir(parents=True)
        checksum = hashlib.sha256()
        with (
          client.stream("GET", asset.browser_download_url) as download,
          path.open("wb") as handle,
        ):
          download.raise_for_status()
          for chunk in download.iter_bytes():
            checksum.update(chunk)
            handle.write(chunk)
        if checksum.hexdigest() != digest:
          raise ExtensionAcquisitionError(
            "Python wheel SHA-256 differs from the GitHub attachment"
          )
        record = _read_wheel(path)

    project, wheel_version, _, _ = parse_wheel_filename(asset.name)
    if (
      (record.name, record.version) != (name, version)
      or canonicalize_name(record.python.project) != project
      or str(wheel_version) != record.python.project_version
      or (record.host_sdk.name, record.host_sdk.version)
      != (manifest.host_sdk, manifest.host_sdk_version)
    ):
      raise ExtensionAcquisitionError(
        "Wheel metadata differs from the selected GitHub Release"
      )
    descriptor = release_from_record(record)
    require_python_association(descriptor)
    return descriptor, path
  except (httpx.HTTPError, BadZipFile, OSError, ValueError) as error:
    raise ExtensionAcquisitionError(
      f"Could not resolve GitHub wheel for {name}@{version}"
    ) from error
