"""Legacy import; use inkcre_core for new consumers."""

from app.core import CORE

ASYNC_DB_ENGINE = CORE.engine
AsyncSessionFactory = CORE.sessions

__all__ = ["ASYNC_DB_ENGINE", "AsyncSessionFactory"]
