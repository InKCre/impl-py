"""Run API Service."""

import asyncio
import contextlib
import datetime
import os
import sys
import time

import fastapi
import uvicorn
from fastapi.responses import JSONResponse

# Setup logging
from libs.obsrv.main import close_obsrv, setup_obsrv, start_obsrv

logger = setup_obsrv()
# Setup logging end

# Load flags
SKIP_EXTENSION_START: bool = os.getenv("SKIP_EXTENSION_START", "").lower() in (
  "1",
  "true",
  "yes",
)
# Load flags end

from fastapi.middleware.cors import CORSMiddleware
from app.settings import settings
from app.engine import ASYNC_DB_ENGINE
from app.routes.block import ROUTER as block_router
from app.routes.relation import ROUTER as relation_router
from app.routes.extension import PEER_INBOUND as extension_peer_inbound
from app.routes.extension import ROUTER as extension_router
from app.routes.source import ROUTER as source_router
from app.routes.job import ROUTER as job_router
from app.routes.cron import ROUTER as cron_router
from app.routes.agent import ROUTER as agent_router
from app.routes.ai import ROUTER as ai_router
from app.routes.entities import ROUTER as entities_router
from app.routes.resolver import ROUTER as resolver_router
from app.routes.graph_navigation import ROUTER as graph_navigation_router
from app.routes.retrieval import ROUTER as retrieval_router
from app.routes.peer import ROUTER as peer_router
from app.routes.deployment_config import ROUTER as deployment_config_router
from app.routes.info_base import ROUTER as info_base_router
from app.routes.lexical_retrieval import PEER_INBOUND as lexical_retrieval_peer_inbound
from app.routes.lexical_retrieval import ROUTER as lexical_retrieval_router
from app.routes.organization import PEER_INBOUND as organization_peer_inbound
from app.routes.organization import ROUTER as organization_router
from app.routes.semantic_retrieval import PEER_INBOUND as semantic_retrieval_peer_inbound
from app.routes.semantic_retrieval import ROUTER as semantic_retrieval_router
from app.routes.sink import ROUTER as sink_router
from app.routes.telemetry import ROUTER as telemetry_router
from app.business.source import SourceManager
from app.business.cron import CronManager
from app.business.job import JobManager
from app.business.extension import EXTENSION_HOST
from app.business.peer import PeerManager
from app.business.ai import AIManager
from app.business.sink import SinkManager

# Import core-owned Job contracts before their catalog is synchronized.
from app.business.organization_job import MediaInterpretationJobHandler  # noqa: F401

# Import independent Organization Job contracts before catalog synchronization.
from app.business.organization.jobs import (  # noqa: F401
  DuplicateAssertionJobHandler,
  EvidenceStanceJobHandler,
  ExistingReferentAnchoringJobHandler,
  RefinementJobHandler,
  RuminationJobHandler,
  SupersessionJobHandler,
  SynthesisJobHandler,
)
from app.middleware import LoggingMiddleware, require_peer_jwt
from app.observability import TelemetryMiddleware, initialize_telemetry
from app.schemas.peer import PEER_EXECUTION_HEADER
from app.health import check_database_readiness
from app.runtime import (
  RUNTIME_STATUS,
  RuntimePhase,
  configure_process_restart,
  process_restart_requested,
)


# Import scheduler
from app.scheduler import drain_scheduler, scheduler, start_scheduler, with_trace_id


_CURRENT_SERVER: uvicorn.Server | None = None
_REQUEST_CONDITION = asyncio.Condition()
_PEER_EXECUTION_OPEN = True
_ACTIVE_PEER_REQUESTS = 0


def _request_uvicorn_exit() -> None:
  if _CURRENT_SERVER is not None:
    _CURRENT_SERVER.should_exit = True


configure_process_restart(_request_uvicorn_exit)


async def pause_peer_execution() -> None:
  """Drain Core producers; the Host then closes its own Extension resources."""
  global _PEER_EXECUTION_OPEN
  async with _REQUEST_CONDITION:
    _PEER_EXECUTION_OPEN = False
    await _REQUEST_CONDITION.wait_for(lambda: _ACTIVE_PEER_REQUESTS == 0)
  await drain_runtime_producers()


async def drain_runtime_producers() -> None:
  """Stop work in dependency order before either environment application or exit."""
  if scheduler.running:
    scheduler.pause()
  await JobManager.shutdown()
  await drain_scheduler()
  await SinkManager.shutdown()


EXTENSION_HOST.pause_peer_execution = pause_peer_execution


@contextlib.contextmanager
def bootstrap_step(step: str):
  """Expose the current startup step and measure its actual elapsed time."""
  RUNTIME_STATUS.step = step
  started = time.perf_counter()
  try:
    yield
  finally:
    logger.info(
      "Runtime bootstrap step %s finished in %.3fs",
      step,
      time.perf_counter() - started,
    )


async def bootstrap_runtime(app: fastapi.FastAPI) -> None:
  """Initialize database-backed runtime services after migrations are ready."""
  from app.business.info_base.resolver import register_core_resolvers
  from app.business.agent import register_core_agent_tools
  from app.business.organization import register_core_organization_behaviors
  from app.business.info_base.storage import StorageManager

  # Register this Peer first so extension enablement can resolve its identity.
  with bootstrap_step("peer_registration"):
    await PeerManager.register_self()
    PeerManager.setup_builtin_outbounds()
    PeerManager.register_inbound(semantic_retrieval_peer_inbound)
    PeerManager.register_inbound(lexical_retrieval_peer_inbound)
    PeerManager.register_inbound(organization_peer_inbound)
    PeerManager.register_inbound(extension_peer_inbound)

  await initialize_telemetry()

  # Core decoders exist independently of installed/enabled extensions.
  with bootstrap_step("core_registration"):
    register_core_resolvers()
    register_core_organization_behaviors()
    register_core_agent_tools()

  # Setup built-in storage instances
  with bootstrap_step("storages"):
    await StorageManager.setup_builtin_storages_async()

  if not SKIP_EXTENSION_START:
    with bootstrap_step("extensions"):
      await EXTENSION_HOST.start_enabled(app)
  with bootstrap_step("source_types"):
    await SourceManager.sync_source_types_async()

  # Extensions may register Sink types, but persisted instances run only by intent.
  with bootstrap_step("sinks"):
    await SinkManager.startup(app, PeerManager.get_current_peer_ref())

  with bootstrap_step("job_types"):
    await JobManager.sync_job_types()
    JobManager.start()

  with bootstrap_step("ai_dialects"):
    await AIManager.sync_dialects_async()

  # Publish only after every provider route and runtime-owned capability is ready.
  with bootstrap_step("peer_publication"):
    await PeerManager.refresh_self(settings.peer_lease_ttl_seconds)

  RUNTIME_STATUS.step = "scheduler"
  start_scheduler()

  # Peer-local timers only wake the database-owned Cron and Job lifecycles.
  scheduler.add_job(
    with_trace_id(
      "PeerManager.refresh_self", PeerManager.refresh_self, enable_backend=False
    ),
    "interval",
    seconds=settings.peer_lease_renew_interval_seconds,
    args=[settings.peer_lease_ttl_seconds],
    id="peer.refresh_self",
    replace_existing=True,
  )
  scheduler.add_job(
    with_trace_id("JobManager.check", JobManager.check, enable_backend=False),
    "interval",
    seconds=30,
    next_run_time=datetime.datetime.now(datetime.timezone.utc),
    id="jobs.check",
    replace_existing=True,
  )
  scheduler.add_job(
    with_trace_id(
      "JobManager.check_abort_requests",
      JobManager.check_abort_requests,
      enable_backend=False,
    ),
    "interval",
    seconds=2,
    id="jobs.check_abort_requests",
    replace_existing=True,
  )
  scheduler.add_job(
    with_trace_id("CronManager.check", CronManager.check, enable_backend=False),
    "interval",
    seconds=30,
    id="crons.check",
    replace_existing=True,
  )


async def bootstrap_when_database_is_ready(app: fastapi.FastAPI) -> None:
  """Wait for a migrated database, then initialize the runtime once."""
  retry_seconds = float(os.getenv("RUNTIME_BOOTSTRAP_RETRY_SECONDS", "5"))

  while True:
    database = await asyncio.to_thread(check_database_readiness)
    if database.ready:
      break
    RUNTIME_STATUS.set(RuntimePhase.WAITING_FOR_DATABASE, database.reason)
    await asyncio.sleep(retry_seconds)

  RUNTIME_STATUS.set(RuntimePhase.STARTING, "runtime_bootstrap_pending")
  try:
    await bootstrap_runtime(app)
  except Exception:
    RUNTIME_STATUS.set(
      RuntimePhase.FAILED, "runtime_bootstrap_failed", step=RUNTIME_STATUS.step
    )
    logger.exception("Runtime bootstrap failed")
    return

  RUNTIME_STATUS.set(RuntimePhase.READY, "ready")
  logger.info("Runtime bootstrap completed")


@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
  start_obsrv()
  logger.info("Application startup")
  RUNTIME_STATUS.set(RuntimePhase.STARTING, "runtime_bootstrap_pending")
  bootstrap_task = asyncio.create_task(bootstrap_when_database_is_ready(app))

  try:
    yield
  finally:
    try:
      logger.info("Application shutdown")
      runtime_was_ready = RUNTIME_STATUS.ready
      RUNTIME_STATUS.set(RuntimePhase.STOPPING, "application_shutdown")
      bootstrap_task.cancel()
      with contextlib.suppress(asyncio.CancelledError):
        await bootstrap_task
      await drain_runtime_producers()
      await EXTENSION_HOST.close_running()
      if scheduler.running:
        scheduler.shutdown(wait=True)
      if runtime_was_ready:
        await PeerManager.clear_self_lease()
    finally:
      try:
        await close_obsrv()
      finally:
        await ASYNC_DB_ENGINE.dispose()


api_app = fastapi.FastAPI(title="InKCre", lifespan=lifespan)


@api_app.middleware("http")
async def peer_execution_gate(request: fastapi.Request, call_next):
  """Reject new work while a Python environment application is in progress."""
  global _ACTIVE_PEER_REQUESTS
  path = request.url.path
  control_path = (
    path in {"/livez", "/readyz"}
    or path.startswith("/extension-management/")
    or path.startswith("/extensions/")
  )
  if control_path:
    return await call_next(request)
  async with _REQUEST_CONDITION:
    if not _PEER_EXECUTION_OPEN:
      return JSONResponse(
        status_code=503,
        content={"detail": "Peer execution is paused for Python environment application"},
      )
    _ACTIVE_PEER_REQUESTS += 1
  try:
    return await call_next(request)
  finally:
    async with _REQUEST_CONDITION:
      _ACTIVE_PEER_REQUESTS -= 1
      _REQUEST_CONDITION.notify_all()


# 添加日志中间件
api_app.add_middleware(LoggingMiddleware)
api_app.add_middleware(TelemetryMiddleware)

# 添加CORS中间件以支持跨域请求
api_app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],  # 在生产环境中应该设置为具体的域名
  allow_credentials=True,
  allow_methods=["*"],  # 允许所有HTTP方法
  allow_headers=["*"],  # 允许所有请求头
  expose_headers=[PEER_EXECUTION_HEADER],
)


# Set up routes
@api_app.get("/livez")
def liveness() -> dict[str, str]:
  """Report process liveness without checking dependencies."""
  return {"status": "ok"}


api_app.get("/heartbeat", include_in_schema=False)(liveness)


@api_app.get("/readyz")
async def readiness() -> JSONResponse:
  """Report database and runtime compatibility without mutating either."""
  database = await asyncio.to_thread(check_database_readiness)
  runtime = RUNTIME_STATUS.as_dict()
  ready = database.ready and RUNTIME_STATUS.ready
  return JSONResponse(
    status_code=200 if ready else 503,
    content={
      "status": "ready" if ready else "not_ready",
      "database": database.as_dict(),
      "runtime": runtime,
    },
  )


core_router = fastapi.APIRouter(
  dependencies=[fastapi.Depends(require_peer_jwt)],
)

core_router.include_router(block_router)
core_router.include_router(relation_router)
core_router.include_router(extension_router)
core_router.include_router(source_router)
core_router.include_router(job_router)
core_router.include_router(cron_router)
core_router.include_router(agent_router)
core_router.include_router(ai_router)
core_router.include_router(entities_router)
core_router.include_router(resolver_router)
core_router.include_router(graph_navigation_router)
core_router.include_router(retrieval_router)
core_router.include_router(peer_router)
core_router.include_router(deployment_config_router)
core_router.include_router(info_base_router)
core_router.include_router(organization_router)
core_router.include_router(semantic_retrieval_router)
core_router.include_router(lexical_retrieval_router)
core_router.include_router(sink_router)
core_router.include_router(telemetry_router)
api_app.include_router(core_router)

if __name__ == "__main__":

  async def serve() -> None:
    global _CURRENT_SERVER
    config = uvicorn.Config(api_app, host=settings.host, port=settings.port)
    _CURRENT_SERVER = uvicorn.Server(config)
    await _CURRENT_SERVER.serve()
    if process_restart_requested():
      os.execv(sys.executable, [sys.executable, *sys.argv])

  asyncio.run(serve())
