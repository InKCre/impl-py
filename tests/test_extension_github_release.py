"""The GitHub Release transport stages only its exact native wheel."""

from functools import partial
import hashlib
from io import BytesIO
import json
from types import SimpleNamespace
import typing
from zipfile import ZipFile

import httpx
import pytest

from app.business.extension import github_release
from app.business.extension.errors import ExtensionAcquisitionError


REPOSITORY = "https://github.com/publisher/extensions"
TAG = "inkcre-ext/publisher/sample/python/v1.2.3"
FILENAME = "inkcre_ext_sample-1.2.3-py3-none-any.whl"
ASSET_URL = f"{REPOSITORY}/releases/download/{TAG}/{FILENAME}"


@pytest.mark.parametrize("valid_digest", [True, False])
def test_stage_release_checks_exact_wheel_and_github_digest(
  monkeypatch: pytest.MonkeyPatch, tmp_path, valid_digest: bool
) -> None:
  record = {
    "schema_version": 1,
    "name": "publisher/sample",
    "version": "1.2.3",
    "host_sdk": {"name": "core-py", "version": ">=0.2.0 <0.4.0"},
    "python": {
      "project": "inkcre-ext-sample",
      "project_version": "1.2.3",
      "entry_point": {
        "group": "inkcre.core.extensions",
        "name": "sample",
        "object": "inkcre_ext_sample:Sample",
      },
    },
  }
  manifest = {
    "schema_version": 1,
    "source_path": ".",
    "host_sdk": "core-py",
    "host_sdk_version": ">=0.2.0 <0.4.0",
  }
  archive = BytesIO()
  with ZipFile(archive, "w") as wheel:
    wheel.writestr(
      "inkcre_ext_sample-1.2.3.dist-info/inkcre-extension.json", json.dumps(record)
    )
  wheel_bytes = archive.getvalue()
  digest = hashlib.sha256(wheel_bytes).hexdigest() if valid_digest else "0" * 64
  requests: list[str] = []

  def handle(request: httpx.Request) -> httpx.Response:
    requests.append(str(request.url))
    if request.url.host == "api.github.com":
      assert request.url.raw_path.endswith(
        b"/releases/tags/inkcre-ext%2Fpublisher%2Fsample%2Fpython%2Fv1.2.3"
      )
      return httpx.Response(
        json={
          "tag_name": TAG,
          "body": f"Release notes\n\n<!-- inkcre-release\n{json.dumps(manifest)}\n-->",
          "draft": False,
          "assets": [
            {"name": "legacy-manifest.json", "browser_download_url": f"{ASSET_URL}.json"},
            {
              "name": FILENAME,
              "browser_download_url": ASSET_URL,
              "digest": f"sha256:{digest}",
            },
          ],
        },
        status_code=200,
      )
    assert str(request.url) == ASSET_URL
    return httpx.Response(200, content=wheel_bytes)

  monkeypatch.setattr(
    github_release.httpx,
    "Client",
    partial(httpx.Client, transport=httpx.MockTransport(handle)),
  )
  if valid_digest:
    descriptor, wheel = github_release.stage_github_distribution(
      REPOSITORY, "publisher/sample", "1.2.3", tmp_path, installed=None
    )
    assert descriptor.python is not None
    assert descriptor.python.project == "inkcre-ext-sample"
    assert wheel is not None and wheel.read_bytes() == wheel_bytes
    metadata = {
      "url": wheel.as_uri(),
      "archive_info": {"hashes": {"sha256": digest}},
    }
    installed = typing.cast(
      github_release.AcquiredDistribution,
      SimpleNamespace(
        distribution=SimpleNamespace(read_text=lambda _: json.dumps(metadata))
      ),
    )
    assert github_release.installed_from_repository(installed, REPOSITORY)
    assert not github_release.installed_from_repository(
      installed, "https://github.com/other/extensions"
    )
  else:
    with pytest.raises(ExtensionAcquisitionError, match="SHA-256"):
      github_release.stage_github_distribution(
        REPOSITORY, "publisher/sample", "1.2.3", tmp_path, installed=None
      )
  assert len(requests) == 2
