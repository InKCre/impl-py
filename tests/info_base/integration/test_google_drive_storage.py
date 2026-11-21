"""Exercise Drive storage over HTTP with a local subset of the Drive v3 and OAuth APIs."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
from secrets import token_urlsafe

from aiohttp import BodyPartReader, web
import pytest
from yarl import URL

from inkcre_core.business.info_base.storage import StorageManager
from inkcre_core.business.info_base.storage import google_drive
from inkcre_core.business.info_base.storage.http import StorageContentTooLargeError
from inkcre_core.schemas.info_base.storage import StorageModel


@dataclass
class _File:
  metadata: dict
  content: bytes


@dataclass
class _Upload:
  file_id: str
  metadata: dict
  expected_size: int
  creates_file: bool


class _DriveFixture:
  def __init__(self):
    self.config = {
      "folder_id": "fixture-folder",
      "client_id": "fixture-client",
      "client_secret": token_urlsafe(),
      "refresh_token": token_urlsafe(),
    }
    self.access_token = token_urlsafe()
    self.origin = URL()
    self.allocated: set[str] = set()
    self.files: dict[str, _File] = {}
    self.uploads: dict[str, _Upload] = {}
    self.fail_after_upload = False
    self.location_host = "127.0.0.1"

  async def refresh(self, request: web.Request) -> web.Response:
    assert "Authorization" not in request.headers
    assert await request.post() == {
      "client_id": self.config["client_id"],
      "client_secret": self.config["client_secret"],
      "refresh_token": self.config["refresh_token"],
      "grant_type": "refresh_token",
    }
    return web.json_response(
      {"access_token": self.access_token, "token_type": "Bearer", "expires_in": 3600}
    )

  def authorize(self, request: web.Request) -> None:
    assert request.headers.get("Authorization") == f"Bearer {self.access_token}"
    assert "Cookie" not in request.headers

  async def generate_ids(self, request: web.Request) -> web.Response:
    self.authorize(request)
    assert request.query["count"] == "1"
    file_id = f"drive_{len(self.allocated) + 1}"
    self.allocated.add(file_id)
    return web.json_response({"ids": [file_id], "space": "drive"})

  async def file(self, request: web.Request) -> web.Response:
    self.authorize(request)
    assert request.query["supportsAllDrives"] == "true"
    file_id = request.match_info["file_id"]
    current = self.files.get(file_id)
    if current is None:
      return web.Response(status=404)
    if request.method == "DELETE":
      del self.files[file_id]
      return web.Response(status=204)
    if request.query.get("alt") == "media":
      return web.Response(body=current.content, content_type="application/octet-stream")
    return web.json_response(
      {
        **current.metadata,
        "trashed": False,
        "sha256Checksum": hashlib.sha256(current.content).hexdigest(),
      }
    )

  async def start_upload(self, request: web.Request) -> web.Response:
    self.authorize(request)
    assert request.query["supportsAllDrives"] == "true"
    upload_type = request.query["uploadType"]
    if upload_type == "multipart":
      reader = await request.multipart()
      metadata_part = await reader.next()
      assert isinstance(metadata_part, BodyPartReader)
      metadata = await metadata_part.json()
      media_part = await reader.next()
      assert isinstance(media_part, BodyPartReader)
      assert media_part.headers["Content-Type"] == "application/octet-stream"
      content = await media_part.read()
      assert await reader.next() is None
    else:
      assert upload_type == "resumable"
      assert request.headers["X-Upload-Content-Type"] == "application/octet-stream"
      metadata = await request.json()
      content = None

    assert isinstance(metadata, dict)
    creates_file = request.method == "POST"
    file_id = metadata["id"] if creates_file else request.match_info["file_id"]
    if creates_file:
      assert file_id in self.allocated
      if file_id in self.files:
        return web.Response(status=409)
      assert metadata["parents"] == [self.config["folder_id"]]
      assert metadata["mimeType"] == "application/octet-stream"
    elif file_id not in self.files:
      return web.Response(status=404)

    if content is not None:
      return self.commit(file_id, metadata, content, creates_file)

    upload_id = token_urlsafe()
    self.uploads[upload_id] = _Upload(
      file_id, metadata, int(request.headers["X-Upload-Content-Length"]), creates_file
    )
    session_path = request.path if creates_file else request.path.rsplit("/", 1)[0]
    location = self.origin.with_host(self.location_host).with_path(session_path)
    location = location.with_query(uploadType="resumable", upload_id=upload_id)
    return web.Response(headers={"Location": str(location)})

  async def finish_upload(self, request: web.Request) -> web.Response:
    self.authorize(request)
    upload = self.uploads.pop(request.query["upload_id"])
    payload = await request.read()
    assert request.content_type == "application/octet-stream"
    assert request.content_length == len(payload) == upload.expected_size
    return self.commit(upload.file_id, upload.metadata, payload, upload.creates_file)

  def commit(
    self, file_id: str, metadata: dict, payload: bytes, creates_file: bool
  ) -> web.Response:
    if creates_file:
      self.files[file_id] = _File(metadata, payload)
    elif file_id in self.files:
      self.files[file_id].content = payload
    else:
      return web.Response(status=404)
    if self.fail_after_upload:
      self.fail_after_upload = False
      return web.Response(status=503)
    return web.json_response({"id": file_id})


@asynccontextmanager
async def _serve_drive(monkeypatch):
  drive = _DriveFixture()
  app = web.Application(client_max_size=8 * 1024 * 1024)
  files = "/drive/v3/files"
  upload = "/upload/drive/v3/files"
  app.router.add_post("/token", drive.refresh)
  app.router.add_get(f"{files}/generateIds", drive.generate_ids)
  app.router.add_get(f"{files}/{{file_id}}", drive.file)
  app.router.add_delete(f"{files}/{{file_id}}", drive.file)
  app.router.add_post(upload, drive.start_upload)
  app.router.add_patch(f"{upload}/{{file_id}}", drive.start_upload)
  app.router.add_put(upload, drive.finish_upload)
  app.router.add_put(f"{upload}/{{file_id}}", drive.finish_upload)
  runner = web.AppRunner(app)
  await runner.setup()
  try:
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    drive.origin = URL(f"http://127.0.0.1:{runner.addresses[0][1]}")
    monkeypatch.setattr(google_drive, "_TOKEN_URL", drive.origin.with_path("/token"))
    monkeypatch.setattr(google_drive, "_FILES_URL", drive.origin.with_path(files))
    monkeypatch.setattr(google_drive, "_UPLOAD_URL", drive.origin.with_path(upload))
    yield drive
  finally:
    await runner.cleanup()


def _storage(config: dict, storage_id: int = 41) -> google_drive.GoogleDriveStorage:
  handler = StorageManager.from_record(
    StorageModel(id=storage_id, type="google_drive", config=config)
  )
  assert isinstance(handler, google_drive.GoogleDriveStorage)
  return handler


def test_drive_round_trip_including_large_and_empty_objects(monkeypatch, async_runner):
  async def run():
    async with _serve_drive(monkeypatch) as drive:
      writer = _storage(drive.config)
      reader = _storage(drive.config)
      payload = b"\x00\xff" * (3 * 1024 * 1024)
      pointer = await writer.write_content(payload, None)
      assert await reader.get_raw_content(pointer) == payload

      limited_reader = _storage({**drive.config, "max_bytes": len(payload) - 1})
      with pytest.raises(StorageContentTooLargeError):
        await limited_reader.get_raw_content(pointer)

      assert await writer.update_content(pointer, b"", None)
      assert await reader.get_raw_content(pointer) == b""
      assert await writer.update_content(pointer, b"\x00replacement\xff", None)
      assert await reader.get_raw_content(pointer) == b"\x00replacement\xff"
      assert await writer.delete_content(pointer, None)
      assert await writer.delete_content(pointer, None)
      assert not await writer.update_content(pointer, b"not recreated", None)
      with pytest.raises(LookupError):
        await reader.get_raw_content(pointer)
      assert not drive.files

      # Unadopted bytes are removed by the same external storage lifecycle.
      async with writer.prepare_write(b""):
        assert len(drive.files) == 1
      assert not drive.files

  async_runner.run(run())


def test_drive_recovers_committed_create_without_using_a_new_id(monkeypatch, async_runner):
  async def run():
    async with _serve_drive(monkeypatch) as drive:
      drive.fail_after_upload = True
      storage = _storage(drive.config)
      pointer = await storage.write_content(b"\x00saved\xff", None)
      assert await storage.get_raw_content(pointer) == b"\x00saved\xff"
      assert len(drive.allocated) == len(drive.files) == 1

  async_runner.run(run())


def test_drive_refuses_to_modify_files_outside_its_scope(monkeypatch, async_runner):
  async def run():
    async with _serve_drive(monkeypatch) as drive:
      storage = _storage(drive.config)
      pointer = await storage.write_content(b"preserve", None)
      wrong_storage = _storage(drive.config, storage_id=42)
      assert not await wrong_storage.delete_content(pointer, None)
      assert not await wrong_storage.update_content(pointer, b"foreign write", None)

      moved_file = next(iter(drive.files.values()))
      moved_file.metadata["parents"] = ["other-folder"]
      assert not await storage.delete_content(pointer, None)
      assert not await storage.update_content(pointer, b"foreign write", None)
      with pytest.raises(LookupError):
        await storage.get_raw_content(pointer)
      assert moved_file.content == b"preserve"

  async_runner.run(run())


def test_drive_rejects_upload_session_on_a_different_origin(monkeypatch, async_runner):
  async def run():
    async with _serve_drive(monkeypatch) as drive:
      drive.location_host = "localhost"
      with pytest.raises(OSError, match="unexpected upload session URL"):
        await _storage(drive.config).write_content(b"no upload", None)
      assert not drive.files

  async_runner.run(run())
