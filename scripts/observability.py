"""Initialize a deployment telemetry identity without enabling any Peer or replacing it."""

import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg.types.json import Jsonb
from app.database_contract.connection import database_connection
from inkcre_core.schemas.observability import CONFIG_KEY, CONFIG_SCHEMA, ObservabilityConfig


def main() -> None:
  candidate = ObservabilityConfig(deployment_id=uuid.uuid4())
  with database_connection() as connection:
    connection.execute(
      "INSERT INTO inkcre.configs (key, schema, value) VALUES (%s, %s, %s) "
      "ON CONFLICT (key) DO NOTHING",
      (CONFIG_KEY, CONFIG_SCHEMA, Jsonb(candidate.model_dump(mode="json"))),
    )
    row = connection.execute(
      "SELECT schema, value FROM inkcre.configs WHERE key=%s", (CONFIG_KEY,)
    ).fetchone()
    if row is None or row[0] != CONFIG_SCHEMA:
      raise ValueError("Existing observability configuration has an unexpected schema")
    config = ObservabilityConfig.model_validate(row[1])
  print(json.dumps({"deployment_id": str(config.deployment_id), "enabled": False}))


if __name__ == "__main__":
  main()
