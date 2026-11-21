"""Exercise SFTP storage over actual SSH against a temporary loopback server."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from os import fsencode
from pathlib import Path
from secrets import token_urlsafe

import asyncssh
import pytest

from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.info_base.storage.sftp import SFTPStorage
from inkcre_core.schemas.info_base.storage import StorageModel


type StorageConfig = dict[str, str | int]


@asynccontextmanager
async def _serve_sftp(
  root: Path,
  *,
  use_key: bool = False,
  before_replace: Callable[[], Awaitable[None]] | None = None,
) -> AsyncGenerator[StorageConfig, None]:
  (root / "objects").mkdir()
  host = "127.0.0.1"
  host_key = asyncssh.generate_private_key("ssh-ed25519")
  expected_username = "storage-fixture"
  expected_password = token_urlsafe()
  client_key = asyncssh.generate_private_key("ssh-ed25519") if use_key else None

  class SSHServer(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
      return True

    def password_auth_supported(self) -> bool:
      return client_key is None

    def validate_password(self, username: str, password: str) -> bool:
      return username == expected_username and password == expected_password

    def public_key_auth_supported(self) -> bool:
      return client_key is not None

    def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
      return (
        username == expected_username
        and client_key is not None
        and key.export_public_key() == client_key.export_public_key()
      )

  class FileServer(asyncssh.SFTPServer):
    def __init__(self, chan: asyncssh.SSHServerChannel):
      super().__init__(chan, chroot=fsencode(root))

    async def posix_rename(self, oldpath: bytes, newpath: bytes) -> None:
      if before_replace is not None:
        await before_replace()
      super().posix_rename(oldpath, newpath)

  async with asyncssh.listen(
    host,
    0,
    config=None,
    server_factory=SSHServer,
    server_host_keys=[host_key],
    sftp_factory=FileServer,
  ) as listener:
    port = listener.get_port()
    public_key = host_key.export_public_key().decode("ascii").strip()
    config: StorageConfig = {
      "host": host,
      "port": port,
      "username": expected_username,
      "known_hosts": f"[{host}]:{port} {public_key}\n",
      "directory": "/objects",
      "timeout": 10,
    }
    if client_key is None:
      config["password"] = expected_password
    else:
      passphrase = token_urlsafe()
      config["private_key"] = client_key.export_private_key(passphrase=passphrase).decode(
        "ascii"
      )
      config["private_key_passphrase"] = passphrase
    yield config


def _storage(config: StorageConfig) -> SFTPStorage:
  handler = StorageManager.from_record(StorageModel(id=1, type="sftp", config=config))
  assert isinstance(handler, SFTPStorage)
  return handler


@pytest.mark.parametrize("use_key", [False, True], ids=["password", "encrypted-key"])
def test_sftp_storage_round_trip(tmp_path, async_runner, use_key):
  async def run():
    async with _serve_sftp(tmp_path, use_key=use_key) as config:
      writer = _storage(config)
      reader = _storage(config)
      contents = b"\x00\xffcontent\r\n"
      pointer = await writer.write_content(contents, None)
      assert await reader.get_raw_content(pointer) == contents

      replacement = b"\x00updated\xfe"
      assert await writer.update_content(pointer, replacement, None)
      assert await reader.get_raw_content(pointer) == replacement

      assert await writer.delete_content(pointer, None)
      assert await writer.delete_content(pointer, None)
      with pytest.raises(LookupError):
        await reader.get_raw_content(pointer)

  async_runner.run(run())


def test_sftp_update_does_not_restore_deleted_object(tmp_path, async_runner):
  async def run():
    reached_replace = asyncio.Event()
    resume_replace = asyncio.Event()

    async def pause_before_replace() -> None:
      reached_replace.set()
      await resume_replace.wait()

    async with _serve_sftp(tmp_path, before_replace=pause_before_replace) as config:
      writer = _storage(config)
      remover = _storage(config)
      pointer = await writer.write_content(b"before", None)
      async with asyncio.TaskGroup() as tasks:
        update = tasks.create_task(writer.update_content(pointer, b"after", None))
        try:
          await asyncio.wait_for(reached_replace.wait(), timeout=5)
          assert await remover.delete_content(pointer, None)
        finally:
          resume_replace.set()

      assert update.result() is False
      with pytest.raises(LookupError):
        await remover.get_raw_content(pointer)

  async_runner.run(run())


def test_sftp_rejects_untrusted_server_key(tmp_path, async_runner):
  async def run():
    async with _serve_sftp(tmp_path) as config:
      wrong_key = asyncssh.generate_private_key("ssh-ed25519")
      public_key = wrong_key.export_public_key().decode("ascii").strip()
      wrong_known_hosts = f"[{config['host']}]:{config['port']} {public_key}\n"
      storage = _storage({**config, "known_hosts": wrong_known_hosts})
      with pytest.raises(asyncssh.HostKeyNotVerifiable):
        await storage.write_content(b"untrusted", None)
      assert list((tmp_path / "objects").iterdir()) == []

  async_runner.run(run())


def test_sftp_request_deadline_also_bounds_shutdown(tmp_path, async_runner):
  async def run():
    resume_replace = asyncio.Event()
    reached_replace = asyncio.Event()

    async def pause_before_replace() -> None:
      reached_replace.set()
      await resume_replace.wait()

    async with _serve_sftp(tmp_path, before_replace=pause_before_replace) as config:
      writer = _storage({**config, "timeout": 1})
      pointer = await writer.write_content(b"before", None)
      async with asyncio.TaskGroup() as tasks:

        async def expect_timeout() -> None:
          with pytest.raises(TimeoutError):
            await writer.update_content(pointer, b"after", None)

        update = tasks.create_task(expect_timeout())
        try:
          await asyncio.wait_for(reached_replace.wait(), timeout=3)
          completed, _ = await asyncio.wait({update}, timeout=3)
          assert update in completed
        finally:
          resume_replace.set()

  async_runner.run(run())
