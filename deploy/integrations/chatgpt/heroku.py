"""Read-only preflight and post-release checks for this Heroku integration."""

import argparse
import datetime
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from .launcher import validate_config


def api(app, path, *, body=None):
  key = os.environ.get("HEROKU_API_KEY")
  if not key:
    key = subprocess.run(
      ["heroku", "auth:token"], capture_output=True, text=True, check=True
    ).stdout.strip()
  request = urllib.request.Request(
    f"https://api.heroku.com/apps/{app}{path}",
    data=None if body is None else json.dumps(body).encode(),
    headers={
      "Authorization": "Bearer " + key,
      "Accept": "application/vnd.heroku+json; version=3",
      "Content-Type": "application/json",
    },
  )
  with urllib.request.urlopen(request, timeout=20) as response:
    return json.load(response)


def sink_ready(base, config):
  request = urllib.request.Request(
    base.rstrip("/") + f"/sinks/{config['INKCRE_MCP_SINK_ID']}/mcp",
    data=json.dumps(
      {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
          "protocolVersion": "2025-03-26",
          "capabilities": {},
          "clientInfo": {"name": "deployment-check", "version": "1"},
        },
      }
    ).encode(),
    headers={
      "Authorization": "Bearer " + config["INKCRE_MCP_PAT"],
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
    },
  )
  with urllib.request.urlopen(request, timeout=15) as response:
    value = json.load(response)
  return "tools" in value.get("result", {}).get("capabilities", {})


def tunnel_ready(app, dyno):
  session = api(app, "/log-sessions", body={"lines": 1500, "source": "app", "dyno": "web"})
  with urllib.request.urlopen(session["logplex_url"], timeout=20) as response:
    lines = response.read().decode().splitlines()
  started = datetime.datetime.fromisoformat(dyno["created_at"].replace("Z", "+00:00"))
  state = False
  for line in lines:
    if "[chatgpt-tunnel] Tunnel " not in line:
      continue
    try:
      timestamp = datetime.datetime.fromisoformat(line.split()[0].replace("Z", "+00:00"))
    except ValueError:
      continue
    if timestamp < started:
      continue
    if "[chatgpt-tunnel] Tunnel ready" in line:
      state = True
    elif any(
      value in line
      for value in ("Tunnel not ready", "Tunnel exited", "Tunnel client started")
    ):
      state = False
  return state


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("action", choices=("preflight", "verify"))
  parser.add_argument("--app", required=True)
  args = parser.parse_args()
  config = api(args.app, "/config-vars")
  validate_config(config)
  if args.action == "preflight":
    print("ChatGPT runtime configuration present; no credentials printed or changed")
    return
  base = api(args.app, "")["web_url"]
  deadline = time.monotonic() + 180
  while time.monotonic() < deadline:
    try:
      dynos = [d for d in api(args.app, "/dynos") if d["type"] == "web"]
      if len(dynos) != 1:
        raise SystemExit("This integration expects one web dyno")
      dyno = dynos[0]
      if "inkcre-chatgpt/launcher.py" not in dyno["command"]:
        raise SystemExit("The web dyno is not running the ChatGPT composition")
      if (
        dyno["state"] == "up" and sink_ready(base, config) and tunnel_ready(args.app, dyno)
      ):
        print(
          f"ChatGPT integration ready: {args.app}, release v{dyno['release']['version']}, "
          f"Sink {config['INKCRE_MCP_SINK_ID']}, {dyno['size']}"
        )
        return
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
      pass
    time.sleep(3)
  raise SystemExit(
    "Core deployment may be healthy, but ChatGPT integration was not verified"
  )


if __name__ == "__main__":
  main()
