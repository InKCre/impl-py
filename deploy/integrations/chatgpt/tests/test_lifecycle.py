"""Isolated lifecycle tests: no real Core, Tunnel, credentials, or database."""

import importlib.util
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FAKE = """
import os, signal, sys, time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
role, folder, port = sys.argv[1:]
root = Path(folder)
def stop(signum, frame):
    (root / (role + '-stopped')).write_text('yes')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
if role == 'core':
    (root / 'core-pid').write_text(str(os.getpid()))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if (root / 'ready').exists() else 503)
            self.end_headers()
        def log_message(self, *args): pass
    HTTPServer(('127.0.0.1', int(port)), Handler).serve_forever()
else:
    with (root / 'tunnel-starts').open('a') as out:
        out.write(str(os.getpid()) + '\\n')
    while True:
        time.sleep(.02)
"""


def wait_for(predicate, timeout=5):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if predicate():
      return
    time.sleep(0.03)
  raise AssertionError("timed out")


class CompanionTests(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.root = Path(self.tmp.name)
    (self.root / "fake.py").write_text(FAKE)
    with socket.socket() as sock:
      sock.bind(("127.0.0.1", 0))
      self.port = sock.getsockname()[1]
    driver = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from launcher import supervise
root, port = Path(sys.argv[2]), sys.argv[3]
base = [sys.executable, str(root/'fake.py')]
raise SystemExit(supervise(base+['core',str(root),port],
                          base+['tunnel',str(root),port], os.environ.copy(),
                          'http://127.0.0.1:'+port+'/readyz', root/'health.url',
                          retry_seconds=.1, poll_seconds=.02))
"""
    self.process = subprocess.Popen(  # noqa: S603 -- local test programs, no shell
      [sys.executable, "-c", driver, str(ROOT), str(self.root), str(self.port)],
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      text=True,
    )
    wait_for(lambda: (self.root / "core-pid").exists())

  def tearDown(self):
    if self.process.poll() is None:
      self.process.terminate()
      self.process.wait(timeout=5)
    assert self.process.stdout is not None
    self.process.stdout.close()
    self.tmp.cleanup()

  def tunnel_pids(self):
    path = self.root / "tunnel-starts"
    return [int(x) for x in path.read_text().splitlines()] if path.exists() else []

  def begin_tunnel(self):
    (self.root / "ready").touch()
    wait_for(lambda: len(self.tunnel_pids()) == 1)

  def test_waits_for_core_and_sigterm_stops_both(self):
    time.sleep(0.15)
    self.assertEqual(self.tunnel_pids(), [])
    self.begin_tunnel()
    self.process.terminate()
    self.assertEqual(self.process.wait(timeout=5), 0)
    self.assertTrue((self.root / "core-stopped").exists())
    self.assertTrue((self.root / "tunnel-stopped").exists())

  def test_tunnel_crash_restarts_only_tunnel(self):
    self.begin_tunnel()
    core_pid = int((self.root / "core-pid").read_text())
    os.kill(self.tunnel_pids()[0], signal.SIGKILL)
    wait_for(lambda: len(self.tunnel_pids()) == 2)
    self.assertIsNone(self.process.poll())
    self.assertEqual(int((self.root / "core-pid").read_text()), core_pid)
    os.kill(core_pid, 0)

  def test_core_crash_stops_tunnel_and_exits(self):
    self.begin_tunnel()
    os.kill(int((self.root / "core-pid").read_text()), signal.SIGKILL)
    self.assertEqual(self.process.wait(timeout=5), 137)
    self.assertTrue((self.root / "tunnel-stopped").exists())


class ConfigurationTests(unittest.TestCase):
  def test_credentials_are_env_references_and_target_is_local(self):
    spec = importlib.util.spec_from_file_location("companion_test", ROOT / "launcher.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.dict(
      os.environ,
      {
        "PORT": "9999",
        "INKCRE_TUNNEL_ID": "test-tunnel",
        "INKCRE_TUNNEL_API_KEY": "fake-secret-key",
        "INKCRE_MCP_PAT": "fake-secret-pat",
        "INKCRE_MCP_SINK_ID": "7",
      },
      clear=True,
    ):
      with patch.object(module, "supervise", return_value=0) as run:
        self.assertEqual(module.main(), 0)
    core, command, env, ready_url, health = run.call_args.args
    self.assertNotIn("fake-secret", " ".join(command))
    self.assertIn("--mcp.server-url=http://127.0.0.1:9999/sinks/7/mcp", command)
    self.assertEqual(env["INKCRE_MCP_AUTHORIZATION"], "Bearer fake-secret-pat")
    self.assertEqual(env["CONTROL_PLANE_API_KEY"], "fake-secret-key")
    self.assertEqual(ready_url, "http://127.0.0.1:9999/readyz")


if __name__ == "__main__":
  unittest.main()
