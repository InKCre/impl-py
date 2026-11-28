"""Render's external replacement and deployment contracts, without live services."""

import json

import httpx
import pytest

from scripts.deploy_render_neon import (
  RenderAPIError,
  RenderClient,
  RenderService,
  _deploy_exact_commit,
)


def test_environment_replacement_preserves_unrelated_values_across_pages():
  written = []
  first: list[dict[str, object]] = [
    {"envVar": {"key": f"setting-{i}", "value": str(i)}} for i in range(100)
  ]
  first[-1]["cursor"] = "next-page"

  def render(request):
    assert request.url.path == "/v1/services/srv-core/env-vars"
    if request.method == "PUT":
      written.append(json.loads(request.content))
    elif request.url.params.get("cursor") == "next-page":
      return httpx.Response(
        200, json=[{"envVar": {"key": "operator-setting", "value": "keep"}}]
      )
    else:
      return httpx.Response(200, json=first)
    return httpx.Response(200)

  with RenderClient("fixture", transport=httpx.MockTransport(render)) as client:
    client.merge_environment("srv-core", {"setting-0": "new"})

  [payload] = written
  expected = {f"setting-{i}": str(i) for i in range(100)}
  expected.update({"setting-0": "new", "operator-setting": "keep"})
  assert {item["key"]: item["value"] for item in payload} == expected


@pytest.mark.parametrize(
  "reported_commit", ["a" * 40, "c" * 40], ids=["converged", "wrong-commit"]
)
def test_auto_deployed_branch_tip_must_converge_to_requested_commit(reported_commit):
  desired = "a" * 40
  service = RenderService(
    "srv-core", "example-core", "https://example.test", "docker", "free", "oregon"
  )
  submissions = []

  def render(request):
    if request.url.path == "/v1/services/srv-core/deploys/initial":
      return httpx.Response(200, json={"status": "live", "commit": {"id": "b" * 40}})
    if request.method == "POST" and request.url.path == "/v1/services/srv-core/deploys":
      submissions.append(json.loads(request.content)["commitId"])
      return httpx.Response(201, json={"id": "replacement"})
    assert request.url.path == "/v1/services/srv-core/deploys/replacement"
    return httpx.Response(200, json={"status": "live", "commit": {"id": reported_commit}})

  with RenderClient("fixture", transport=httpx.MockTransport(render)) as client:
    if reported_commit == desired:
      _deploy_exact_commit(client, service, "initial", desired, wait_seconds=1)
    else:
      with pytest.raises(RenderAPIError, match="did not converge to the exact commit"):
        _deploy_exact_commit(client, service, "initial", desired, wait_seconds=1)
  assert submissions == [desired]
