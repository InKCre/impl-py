"""Access logs retain request diagnostics without query credentials."""

from copy import deepcopy
import asyncio
import logging
from pathlib import Path
import socket
import subprocess
import sys

import pytest
import uvicorn
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter

from app.access_log import QuerylessAccessFilter, access_log_config


@pytest.mark.parametrize(
  ("target", "path"),
  [
    ("/callback", "/callback"),
    ("/callback?oauth_token=synthetic&oauth_verifier=synthetic", "/callback"),
    ("/a%3Fb?search=synthetic", "/a%3Fb"),
  ],
)
@pytest.mark.parametrize("status", [200, 302, 404, 500])
def test_access_record_omits_query(target: str, path: str, status: int) -> None:
  record = logging.LogRecord(
    "uvicorn.access",
    logging.INFO,
    __file__,
    0,
    '%s - "%s %s HTTP/%s" %d',
    ("127.0.0.1:1234", "GET", target, "1.1", status),
    None,
  )
  assert QuerylessAccessFilter().filter(record)
  assert record.getMessage() == f'127.0.0.1:1234 - "GET {path} HTTP/1.1" {status}'
  output = AccessFormatter(
    '%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False
  ).format(record)
  assert f'"GET {path} HTTP/1.1" {status}' in output
  assert "synthetic" not in output
  assert "?" not in output


def test_access_configs_are_independent() -> None:
  original = deepcopy(LOGGING_CONFIG)
  first, second = access_log_config(), access_log_config()
  first["loggers"]["uvicorn.access"]["filters"].clear()
  assert second["loggers"]["uvicorn.access"]["filters"] == ["queryless_access"]
  assert LOGGING_CONFIG == original


def test_real_uvicorn_access_records() -> None:
  # Uvicorn's dictConfig closes existing handlers, so isolate its whole process.
  result = subprocess.run(  # noqa: S603
    [
      sys.executable,
      "-c",
      "import runpy, sys; runpy.run_path(sys.argv[1], run_name='__main__')",
      str(Path(__file__).resolve()),
    ],
    cwd=Path(__file__).resolve().parents[1],
    capture_output=True,
    text=True,
    check=True,
    timeout=15,
  )
  output = result.stdout
  assert output.count('"GET /callback HTTP/1.1" 204 No Content') == 2
  assert "?" not in output
  assert "oauth_token" not in output
  assert "synthetic" not in output


if __name__ == "__main__":

  async def app(scope, receive, send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})

  async def probe() -> None:
    with socket.socket() as sock:
      sock.bind(("127.0.0.1", 0))
      sock.listen()
      sock.setblocking(False)
      server = uvicorn.Server(
        uvicorn.Config(
          app, http="h11", lifespan="off", log_config=access_log_config(), use_colors=False
        )
      )
      task = asyncio.create_task(server.serve(sockets=[sock]))
      try:

        async def ready() -> None:
          while not server.started:
            if task.done():
              await task
              raise RuntimeError("server exited before startup")
            await asyncio.sleep(0.01)

        await asyncio.wait_for(ready(), 5)
        for target in ("/callback", "/callback?oauth_token=synthetic"):
          reader, writer = await asyncio.open_connection("127.0.0.1", sock.getsockname()[1])
          try:
            request_line = f"GET {target} HTTP/1.1\r\n"
            writer.write(
              (request_line + "Host: localhost\r\nConnection: close\r\n\r\n").encode()
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 5)
            assert response.startswith(b"HTTP/1.1 204")
          finally:
            writer.close()
            await writer.wait_closed()
      finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)

  asyncio.run(probe())
