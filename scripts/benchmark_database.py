"""On-demand PostgreSQL benchmark for graph submit and batched Block read use cases."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import uuid

import sqlalchemy
import sqlmodel


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from inkcre_core.business.info_base.commands import submit_graph
from inkcre_core.business.info_base.services import get_entity_records
from app.engine import ASYNC_DB_ENGINE, AsyncSessionFactory
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.main import GraphBlockForm, GraphForm, GraphRelationForm


def positive(value: str) -> int:
  number = int(value)
  if number < 1:
    raise argparse.ArgumentTypeError("must be positive")
  return number


def latency(samples: list[float]) -> dict[str, float | int]:
  if not samples:
    return {"samples": 0}
  ordered = sorted(samples)
  return {
    "samples": len(samples),
    "p50_ms": round(statistics.median(ordered) * 1000, 3),
    "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1] * 1000, 3),
  }


async def benchmark(nodes: int, iterations: int, concurrency: int) -> dict:
  marker = f"inkcre-db-benchmark:{uuid.uuid4().hex}:"
  writes: list[float] = []
  reads: list[float] = []
  errors: list[str] = []

  async def operation(index: int, *, measured: bool = True) -> None:
    graph = GraphForm(
      blocks=tuple(
        GraphBlockForm(id=-node, resolver="core.text.v1", content=f"{marker}{index}:{node}")
        for node in range(1, nodes + 1)
      ),
      relations=tuple(
        GraphRelationForm(from_=-node, to_=-(node + 1), content="benchmark edge")
        for node in range(1, nodes)
      ),
    )
    started = time.perf_counter()
    result = await submit_graph(graph)
    submitted = time.perf_counter()
    blocks, _ = await get_entity_records(tuple(mapping.id for mapping in result.blocks), ())
    finished = time.perf_counter()
    if len(blocks) != nodes:
      raise RuntimeError("Committed graph readback is incomplete")
    if measured:
      writes.append(submitted - started)
      reads.append(finished - submitted)

  async def worker(offset: int) -> None:
    for index in range(offset, iterations, concurrency):
      try:
        async with asyncio.timeout(60):
          await operation(index)
      except Exception as error:
        errors.append(type(error).__name__)

  try:
    async with ASYNC_DB_ENGINE.connect() as connection:
      postgres = (
        await connection.execute(sqlalchemy.text("SELECT version()"))
      ).scalar_one()
    # Warm native driver/pool paths; the warmup is excluded from reported latency.
    async with asyncio.timeout(60):
      await operation(-1, measured=False)
    started = time.perf_counter()
    await asyncio.gather(*(worker(index) for index in range(concurrency)))
    elapsed = time.perf_counter() - started
    return {
      "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
      "revision": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
      ).strip(),
      "python": platform.python_version(),
      "platform": platform.platform(),
      "postgresql": postgres,
      "sqlalchemy": importlib.metadata.version("sqlalchemy"),
      "psycopg": importlib.metadata.version("psycopg"),
      "nodes_per_graph": nodes,
      "relations_per_graph": nodes - 1,
      "iterations": iterations,
      "concurrency": concurrency,
      "elapsed_seconds": round(elapsed, 3),
      "successful_graphs_per_second": round(len(reads) / elapsed, 3),
      "submit_graph": latency(writes),
      "read_blocks": latency(reads),
      "errors": errors,
      "error_rate": len(errors) / iterations,
      "scope": (
        "native application use cases; excludes HTTP transport and external providers"
      ),
    }
  finally:
    try:
      # Even a committed write with a lost response is identifiable by this run's UUID.
      async with AsyncSessionFactory.begin() as session:
        await session.execute(
          sqlalchemy.delete(BlockModel).where(
            sqlmodel.col(BlockModel.content).startswith(marker, autoescape=True),
            sqlmodel.col(BlockModel.resolver) == "core.text.v1",
          )
        )
    finally:
      await ASYNC_DB_ENGINE.dispose()


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--nodes", type=positive, default=100)
  parser.add_argument("--iterations", type=positive, default=30)
  parser.add_argument("--concurrency", type=positive, default=4)
  parser.add_argument("--output", type=Path)
  args = parser.parse_args()
  result = asyncio.run(benchmark(args.nodes, args.iterations, args.concurrency))
  document = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
  if args.output:
    args.output.write_text(document)
  print(document, end="")
  return int(bool(result["errors"]))


if __name__ == "__main__":
  raise SystemExit(main())
