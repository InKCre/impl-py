"""Deployment configuration transaction composition."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from inkcre_core.context import current_core
from .repository import DeploymentConfigRepository


@asynccontextmanager
async def configuration_transaction() -> AsyncGenerator[DeploymentConfigRepository, None]:
  async with current_core().sessions.begin() as session:
    yield DeploymentConfigRepository(session)
