"""Enforce runtime persistence ownership in addition to Ruff's banned APIs."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DB_MODULES = ("sqlalchemy", "sqlmodel", "psycopg")
SQL_OPERATIONS = {"select", "insert", "update", "delete", "text"}
SESSION_CONSTRUCTORS = {"Session", "AsyncSession", "sessionmaker", "async_sessionmaker"}
ENGINE_CONSTRUCTORS = {"create_engine", "create_async_engine"}


def inspect_source(path: str, source: str) -> list[str]:
  tree = ast.parse(source)
  imports: dict[str, str] = {}
  errors: list[str] = []
  persistence = path.startswith("inkcre_core/persistence/")
  uow = persistence and path.endswith("/uow.py")
  engine = path == "inkcre_core/context.py"
  repository = persistence and not uow
  business = path.startswith(("app/business/", "inkcre_core/business/", "extensions/"))
  route = path.startswith("app/routes/")

  def report(node: ast.stmt | ast.expr, message: str) -> None:
    errors.append(f"{path}:{node.lineno}: {message}")

  def qualified(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
      return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
      return qualified(node.value) + "." + node.attr
    return ""

  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      for alias in node.names:
        imports[alias.asname or alias.name.split(".")[0]] = (
          alias.name if alias.asname else alias.name.split(".")[0]
        )
    elif isinstance(node, ast.ImportFrom):
      module = node.module or ""
      if node.level:
        package = path.removesuffix(".py").split("/")[:-1]
        module = ".".join(package[: len(package) - node.level + 1] + [module]).rstrip(".")
      for alias in node.names:
        imports[alias.asname or alias.name] = f"{module}.{alias.name}"
    else:
      continue
    names = (
      [alias.name for alias in node.names]
      if isinstance(node, ast.Import)
      else [imports[alias.asname or alias.name] for alias in node.names]
    )
    for name in names:
      if name.startswith("app.engine") and not (uow or path == "run.py"):
        report(node, "数据库工厂只能由 UoW 使用；lifespan 仅拥有 engine 释放。")
      if route and name.startswith(("inkcre_core.persistence", "app.persistence")):
        report(node, "HTTP route 必须调用 business，不直接依赖 persistence。")
      if persistence and name.startswith(("inkcre_core.business", "app.business")):
        report(node, "persistence 不得反向依赖 business。")
      if (business or route) and name.startswith(DB_MODULES):
        if name.rsplit(".", 1)[-1] in SESSION_CONSTRUCTORS | SQL_OPERATIONS:
          report(node, "业务与路由不持有 raw session 或直接 SQL 能力。")
      if name.startswith("tests"):
        report(node, "运行时代码不能导入测试数据库适配器。")

  session_names = {"self._session"}
  for node in ast.walk(tree):
    if isinstance(node, ast.arg) and node.annotation is not None:
      if qualified(node.annotation).endswith(".AsyncSession"):
        session_names.add(node.arg)
  for node in ast.walk(tree):
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
      value = node.value
      if value is not None and qualified(value) in session_names:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        session_names.update(qualified(target) for target in targets)

  for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
      continue
    name = qualified(node.func)
    leaf = name.rsplit(".", 1)[-1]
    if name.startswith(DB_MODULES):
      if leaf in SESSION_CONSTRUCTORS | ENGINE_CONSTRUCTORS and not engine:
        report(node, "Engine 和 session factory 只在 inkcre_core.context 创建。")
      if (business or route) and leaf in SQL_OPERATIONS:
        report(node, "SQL 查询与写入必须位于 persistence。")
    if repository and isinstance(node.func, ast.Attribute):
      if qualified(node.func.value) in session_names and node.func.attr in {
        "begin",
        "begin_nested",
        "commit",
        "rollback",
        "close",
      }:
        report(node, "repository 不拥有 session 或事务生命周期。")
  return errors


def self_check() -> None:
  # Alias and relative-import cases catch realistic accidental boundary leaks.
  assert inspect_source("app/routes/example.py", "from ..persistence.info_base import uow")
  assert inspect_source("app/business/example.py", "import sqlalchemy as sa\nsa.select(1)")
  assert inspect_source(
    "inkcre_core/persistence/x/repository.py",
    """
from sqlalchemy.ext.asyncio import AsyncSession as Session
async def save(session: Session):
  alias = session
  await alias.commit()
""",
  )
  assert not inspect_source(
    "inkcre_core/persistence/x/repository.py",
    """
from sqlalchemy.ext.asyncio import AsyncSession
async def save(session: AsyncSession):
  await session.flush()
""",
  )
  assert not inspect_source(
    "inkcre_core/persistence/x/uow.py",
    """
from inkcre_core.context import current_core
async def operation():
  async with current_core().sessions.begin() as session:
    yield session
""",
  )


def main() -> int:
  self_check()
  # Share Ruff's runtime scope and generated/ignored-file exclusions.
  result = subprocess.run(
    [
      sys.executable,
      "-m",
      "ruff",
      "check",
      "--config",
      "ruff.database.toml",
      "--show-files",
    ],
    cwd=ROOT,
    check=True,
    capture_output=True,
    text=True,
  )
  paths = [Path(line) for line in result.stdout.splitlines()]
  errors = []
  for path in sorted(paths):
    relative = path.relative_to(ROOT).as_posix()
    if relative.startswith("app/database_contract/"):
      continue
    errors.extend(inspect_source(relative, path.read_text()))
  if errors:
    print("\n".join(errors))
    return 1
  print("Runtime database boundaries passed")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
