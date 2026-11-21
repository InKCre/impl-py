"""Exercise named pins against an opt-in, isolated offline Kubo daemon."""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.info_base.storage.ipfs import IPFSStorage
from inkcre_core.schemas.info_base.storage import StorageModel


def _ipfs(binary: str, repo: Path, *args: str) -> str:
  return subprocess.run(  # noqa: S603 - opt-in local executable, invoked without a shell.
    [binary, *args],
    env={**os.environ, "IPFS_PATH": str(repo)},
    capture_output=True,
    text=True,
    check=True,
    timeout=20,
  ).stdout


@contextmanager
def _kubo(binary: str, repo: Path, log: Path) -> Generator[str, None, None]:
  address = repo / "api"
  with (
    log.open("wb") as output,
    subprocess.Popen(  # noqa: S603 - opt-in local executable.
      [binary, "daemon", "--offline"],
      env={**os.environ, "IPFS_PATH": str(repo)},
      stdout=output,
      stderr=subprocess.STDOUT,
    ) as process,
  ):
    try:
      deadline = time.monotonic() + 15
      while not address.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
      assert address.exists(), log.read_text()
      port = address.read_text().strip().removeprefix("/ip4/127.0.0.1/tcp/")
      assert port.isdecimal() and int(port) > 0, address.read_text()
      yield f"http://127.0.0.1:{port}"
    finally:
      if process.poll() is None:
        process.terminate()
        try:
          process.wait(timeout=5)
        except subprocess.TimeoutExpired:
          process.kill()
          process.wait(timeout=5)


def _storage(endpoint: str) -> IPFSStorage:
  store = StorageManager.from_record(
    StorageModel(id=1, type="ipfs", config={"endpoint": endpoint})
  )
  assert isinstance(store, IPFSStorage)
  return store


@pytest.fixture
def kubo_binary() -> str:
  binary = os.getenv("INKCRE_TEST_KUBO")
  if not binary:
    pytest.skip("set INKCRE_TEST_KUBO to a Kubo executable")
  return binary


@pytest.mark.integration
def test_ipfs_pins_keep_duplicate_payloads_independently(
  tmp_path, async_runner, kubo_binary
):
  binary = kubo_binary
  repo = tmp_path / "repo"
  log = tmp_path / "kubo.log"
  _ipfs(binary, repo, "init", "--empty-repo", "--profile=test")
  _ipfs(binary, repo, "config", "Addresses.API", "/ip4/127.0.0.1/tcp/0")
  _ipfs(binary, repo, "config", "Addresses.Gateway", "")
  contents = bytes(range(256)) * 2049

  async def pins() -> set[str]:
    output = await asyncio.to_thread(
      _ipfs, binary, repo, "pin", "ls", "--type=recursive", "--quiet"
    )
    return set(output.splitlines())

  async def write(endpoint: str) -> tuple[str, str]:
    store = _storage(endpoint)
    baseline = await pins()
    first, second = await asyncio.gather(
      store.write_content(contents, None), store.write_content(contents, None)
    )
    first_root = json.loads(first)["root_cid"]
    second_root = json.loads(second)["root_cid"]
    assert first_root != second_root
    owned = baseline | {first_root, second_root}
    assert await pins() == owned
    first_links, second_links = await asyncio.gather(
      asyncio.to_thread(_ipfs, binary, repo, "ls", first_root),
      asyncio.to_thread(_ipfs, binary, repo, "ls", second_root),
    )
    assert first_links.split()[0] == second_links.split()[0]
    assert (
      await store.get_raw_content(first) == await store.get_raw_content(second) == contents
    )

    mismatched = json.dumps(
      {**json.loads(first), "object_id": json.loads(second)["object_id"]}
    )
    with pytest.raises(ValueError, match="does not own"):
      await store.delete_content(mismatched, None)
    with pytest.raises(NotImplementedError, match="immutable"):
      await store.update_content(second, b"replacement", None)

    async with store.prepare_write(contents):
      staged_roots = await pins()
      assert staged_roots > owned
      assert len(staged_roots - owned) == 1
    assert await pins() == owned
    assert await store.delete_content(first, None)
    assert await store.delete_content(first, None)
    with pytest.raises(LookupError, match="not retained"):
      await store.get_raw_content(first)
    assert await pins() == baseline | {second_root}
    return first, second

  with _kubo(binary, repo, log) as endpoint:
    released, retained = async_runner.run(write(endpoint))
    _ipfs(binary, repo, "repo", "gc")

  async def read(endpoint: str) -> None:
    store = _storage(endpoint)
    assert await store.get_raw_content(retained) == contents
    with pytest.raises(LookupError, match="not retained"):
      await store.get_raw_content(released)
    assert await store.delete_content(released, None)

  with _kubo(binary, repo, log) as endpoint:
    async_runner.run(read(endpoint))
