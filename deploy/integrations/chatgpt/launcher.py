"""Run the unchanged Core and its personal MCP Tunnel in one Eco web dyno."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


def log(message):
  print(f"[chatgpt-tunnel] {message}", flush=True)


def ready(url):
  try:
    with urllib.request.urlopen(url, timeout=1) as response:
      return response.status == 200
  except (urllib.error.URLError, TimeoutError, OSError):
    return False


def stop(children, grace=20):
  """Signal process groups so a Tunnel transport child is also stopped."""
  for child in children:
    try:
      os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
      pass
  deadline = time.monotonic() + grace
  for child in children:
    try:
      child.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
      pass
  for child in children:
    try:
      os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
      pass
    child.wait()


def supervise(  # noqa: PLR0913 -- two commands, their environment and local health contract
  core_command,
  tunnel_command,
  tunnel_env,
  ready_url,
  health_file,
  retry_seconds=10,
  poll_seconds=0.25,
):
  stopping = False

  def shutdown(signum, frame):
    nonlocal stopping
    stopping = True

  signal.signal(signal.SIGTERM, shutdown)
  signal.signal(signal.SIGINT, shutdown)
  core = subprocess.Popen(  # noqa: S603 -- argv is constructed locally, without a shell
    core_command, start_new_session=True
  )
  tunnel = None
  retry_at = 0
  backoff = retry_seconds
  started_at = 0
  healthy = None
  check_at = 0.0
  log("Core started; waiting for local readiness")
  try:
    while not stopping:
      code = core.poll()
      if code is not None:
        log(f"Core exited with code {code}; stopping companion")
        return code if code >= 0 else 128 - code
      if tunnel is not None and tunnel.poll() is not None:
        code = tunnel.returncode
        # A failed Tunnel must not restart or terminate a healthy Core.
        stop([tunnel])
        tunnel = None
        healthy = None
        if time.monotonic() - started_at >= 60:
          backoff = retry_seconds
        retry_at = time.monotonic() + backoff
        log(f"Tunnel exited with code {code}; retrying in {backoff}s")
        backoff = min(backoff * 2, 60)
      if tunnel is None and time.monotonic() >= retry_at and ready(ready_url):
        health_file.unlink(missing_ok=True)
        tunnel = subprocess.Popen(  # noqa: S603 -- fixed binary and env references
          tunnel_command, env=tunnel_env, start_new_session=True
        )
        started_at = time.monotonic()
        log(f"Tunnel client started, PID {tunnel.pid}")
      if tunnel is not None and time.monotonic() >= check_at:
        check_at = time.monotonic() + 5
        try:
          address = health_file.read_text().strip()
          connected = address.startswith("http://127.0.0.1:") and ready(address + "/readyz")
        except OSError:
          connected = False
        if connected != healthy:
          log("Tunnel ready" if connected else "Tunnel not ready")
          healthy = connected
      time.sleep(poll_seconds)
    log("Shutdown requested; stopping Core and Tunnel")
    return 0
  finally:
    stop([core] + ([tunnel] if tunnel is not None else []))
    health_file.unlink(missing_ok=True)


def validate_config(config):
  names = (
    "INKCRE_TUNNEL_ID",
    "INKCRE_TUNNEL_API_KEY",
    "INKCRE_MCP_PAT",
    "INKCRE_MCP_SINK_ID",
  )
  missing = [name for name in names if not config.get(name)]
  if missing:
    raise SystemExit("Missing required configuration: " + ", ".join(missing))
  sink_id = config["INKCRE_MCP_SINK_ID"]
  if not sink_id.isdigit() or int(sink_id) < 1:
    raise SystemExit("INKCRE_MCP_SINK_ID must be a positive integer")


def main():
  port = os.environ.get("PORT", "8000")
  if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise SystemExit("PORT must be a valid TCP port")
  validate_config(os.environ)
  sink_id = os.environ["INKCRE_MCP_SINK_ID"]
  local = f"http://127.0.0.1:{port}"
  health_file = Path("/tmp/inkcre-tunnel-health.url")
  environment = os.environ.copy()
  environment["CONTROL_PLANE_API_KEY"] = os.environ["INKCRE_TUNNEL_API_KEY"]
  environment["INKCRE_MCP_AUTHORIZATION"] = "Bearer " + os.environ["INKCRE_MCP_PAT"]
  command = [
    "/opt/inkcre-chatgpt/tunnel-client",
    "run",
    "--control-plane.tunnel-id=" + os.environ["INKCRE_TUNNEL_ID"],
    "--control-plane.api-key=env:CONTROL_PLANE_API_KEY",
    "--mcp.server-url=" + local + f"/sinks/{sink_id}/mcp",
    "--mcp.extra-headers=Authorization: env:INKCRE_MCP_AUTHORIZATION",
    "--mcp.discovery-extra-headers=Authorization: env:INKCRE_MCP_AUTHORIZATION",
    "--health.listen-addr=127.0.0.1:0",
    "--health.url-file=" + str(health_file),
  ]
  return supervise(
    [sys.executable, "scripts/container.py", "web"],
    command,
    environment,
    local + "/readyz",
    health_file,
  )


if __name__ == "__main__":
  raise SystemExit(main())
