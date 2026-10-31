"""Deep runtime for typed durable background Jobs."""

import abc
import asyncio
import dataclasses
import typing

import jsonschema  # pyrefly: ignore[untyped-import]
import pydantic

from app.contracts.jobs import BUILTIN_JOB_TYPES_BY_ID
from app.persistence.job.uow import JobUnitOfWork, job_uow
from app.scheduler import scheduler, with_trace_id
from app.schemas.job import JobID, JobModel, JobStatus, JobTypeID, JobTypeModel
from libs.obsrv.main import get_logger
from libs.obsrv.telemetry import emit_event, operation
from app.observability import submission_context, submission_links
from opentelemetry.context import Context


LOGGER = get_logger().getChild(__name__)
ParametersTV = typing.TypeVar("ParametersTV", bound=pydantic.BaseModel)


class UnknownJobTypeError(ValueError):
  """A submitted exact Job type is absent from the deployment catalog."""


@dataclasses.dataclass
class _Execution:
  task: asyncio.Task
  cancellation_requested: bool = False
  closing: bool = False

  def cancel(self) -> None:
    # Do not interrupt cleanup already started by timeout or an earlier abort.
    if not self.cancellation_requested and not self.closing:
      self.cancellation_requested = True
      if not self.task.cancelling():
        self.task.cancel()


class JobHandler(abc.ABC, typing.Generic[ParametersTV]):
  """One exact executable Job contract registered by its owning module."""

  type: typing.ClassVar[JobTypeID]
  description: typing.ClassVar[str]
  parameters_model: typing.ClassVar[type[pydantic.BaseModel]]
  default_timeout_seconds: typing.ClassVar[int]

  def __init_subclass__(
    cls,
    *,
    job_type: JobTypeID,
    description: str | None = None,
    parameters_model: type[ParametersTV] | None = None,
    default_timeout_seconds: int | None = None,
    **kwargs,
  ) -> None:
    builtin = BUILTIN_JOB_TYPES_BY_ID.get(job_type)
    if builtin is not None:
      if any(
        supplied is not None and supplied != expected
        for supplied, expected in (
          (description, builtin.description),
          (parameters_model, builtin.parameters_model),
          (default_timeout_seconds, builtin.default_timeout_seconds),
        )
      ):
        raise ValueError(f"Built-in Job {job_type!r} must use its shared contract")
      description = builtin.description
      parameters_model = typing.cast(type[ParametersTV], builtin.parameters_model)
      default_timeout_seconds = builtin.default_timeout_seconds
    if description is None or parameters_model is None or default_timeout_seconds is None:
      raise TypeError("Custom Jobs require description, parameters_model and timeout")
    if default_timeout_seconds <= 0:
      raise ValueError("default_timeout_seconds must be positive")
    cls.type = job_type
    cls.description = description
    cls.parameters_model = parameters_model
    cls.default_timeout_seconds = default_timeout_seconds
    JobManager.register_handler(cls)
    super().__init_subclass__(**kwargs)

  @classmethod
  def validate_parameters(cls, parameters: dict[str, typing.Any]) -> ParametersTV:
    return typing.cast(ParametersTV, cls.parameters_model.model_validate(parameters))

  @classmethod
  async def normalize_parameters(
    cls, parameters: dict[str, typing.Any], uow: JobUnitOfWork
  ) -> dict[str, typing.Any]:
    """Validate a new Job/Cron input; owners may resolve nested catalog contracts.

    Execution restores the persisted model through validate_parameters instead;
    it does not repeat submission-only normalization.
    """
    del uow
    return cls.validate_parameters(parameters).model_dump(mode="json")

  @classmethod
  @abc.abstractmethod
  async def can_handle(cls, parameters: ParametersTV) -> bool:
    """Return whether this runtime can execute these parameters now."""
    ...

  @classmethod
  @abc.abstractmethod
  async def handle(cls, job: JobModel, parameters: ParametersTV) -> None:
    """Execute one already claimed Job."""
    ...


class JobManager:
  """Own Job Handler registration, typed creation, claim and terminal closure."""

  _handlers: dict[JobTypeID, type[JobHandler]] = {}
  _active: dict[JobID, _Execution] = {}
  _accepting = True

  @classmethod
  def register_handler(cls, handler: type[JobHandler]) -> None:
    existing = cls._handlers.get(handler.type)
    if existing is handler:
      return
    if existing is not None:
      raise ValueError(
        f"Job type {handler.type!r} is already handled by "
        f"{existing.__module__}.{existing.__qualname__}"
      )
    cls._handlers[handler.type] = handler

  @classmethod
  async def sync_job_types(cls) -> None:
    rows = []
    for handler in cls._handlers.values():
      rows.append(
        dict(
          id=handler.type,
          description=handler.description,
          parameters_schema=handler.parameters_model.model_json_schema(),
          default_timeout_seconds=handler.default_timeout_seconds,
        )
      )
    async with job_uow() as uow:
      await uow.jobs.sync_types(rows)

  @classmethod
  async def normalize_parameters(
    cls,
    job_type: JobTypeID,
    parameters: dict[str, typing.Any],
    uow: JobUnitOfWork,
  ) -> dict[str, typing.Any]:
    handler = cls._handlers.get(job_type)
    if handler is not None:
      return await handler.normalize_parameters(parameters, uow)

    persisted_type = await uow.jobs.get_type(job_type)
    if persisted_type is None:
      raise UnknownJobTypeError(f"Unknown Job type: {job_type}")
    jsonschema.Draft202012Validator(persisted_type.parameters_schema).validate(parameters)
    return parameters

  @classmethod
  async def create(
    cls,
    job_type: JobTypeID,
    parameters: dict[str, typing.Any],
    timeout_seconds: int | None = None,
  ) -> JobModel:
    """Validate and persist one independent pending Job."""
    async with job_uow() as uow:
      job = await cls.create_in_uow(job_type, parameters, timeout_seconds, uow=uow)
    emit_event("job.submitted", {"inkcre.job.id": job.id or 0})
    return job

  @classmethod
  async def create_in_uow(
    cls,
    job_type: JobTypeID,
    parameters: dict[str, typing.Any],
    timeout_seconds: int | None = None,
    *,
    uow: JobUnitOfWork,
  ) -> JobModel:
    normalized = await cls.normalize_parameters(job_type, parameters, uow)
    persisted_type = await uow.jobs.get_type(job_type)
    if persisted_type is None:
      raise UnknownJobTypeError(f"Unknown Job type: {job_type}")
    effective_timeout = (
      persisted_type.default_timeout_seconds if timeout_seconds is None else timeout_seconds
    )
    if effective_timeout <= 0:
      raise ValueError("Job timeout_seconds must be positive")

    with operation("job.submit") as observation:
      span = observation.span
      carrier = submission_context()
      job = JobModel(
        type=job_type,
        parameters=normalized,
        timeout_seconds=effective_timeout,
        submission_traceparent=carrier["submission_traceparent"],
        submission_tracestate=carrier["submission_tracestate"],
      )
      job = await uow.jobs.create(job)
      span.set_attribute("inkcre.job.id", job.id or 0)
      # The composing use case still owns commit; this span only proves the flush.
      span.set_attribute("inkcre.job.write_phase", "flushed")
      return job

  @classmethod
  async def _prepare(
    cls,
    job: JobModel,
  ) -> tuple[type[JobHandler], pydantic.BaseModel] | None:
    handler = cls._handlers.get(job.type)
    if handler is None:
      return None
    parameters = handler.validate_parameters(job.parameters)
    return (handler, parameters) if await handler.can_handle(parameters) else None

  @classmethod
  async def get(cls, job_id: JobID) -> JobModel | None:
    async with job_uow() as uow:
      return await uow.jobs.get(job_id)

  @classmethod
  async def get_type(cls, type_: JobTypeID) -> JobTypeModel | None:
    async with job_uow() as uow:
      return await uow.jobs.get_type(type_)

  @classmethod
  async def list_types(
    cls, *, limit: int | None = None, cursor: str | None = None
  ) -> tuple[list[JobTypeModel], str | None]:
    async with job_uow() as uow:
      return await uow.jobs.list_types(limit=limit, cursor=cursor)

  @classmethod
  async def list_jobs(
    cls,
    *,
    limit: int = 20,
    cursor: JobID | None = None,
    type_: JobTypeID | None = None,
    status: JobStatus | None = None,
  ) -> tuple[list[JobModel], JobID | None]:
    async with job_uow() as uow:
      return await uow.jobs.list_jobs(
        limit=limit, cursor=cursor, type_=type_, status=status
      )

  @classmethod
  async def notify_worker(cls) -> None:
    """Best-effort post-commit hint; scan failure cannot undo accepted work."""
    try:
      await cls.check()
    except Exception:
      LOGGER.exception("Job discovery hint failed; periodic discovery remains active")

  @classmethod
  async def abort(cls, job_id: JobID) -> JobModel | None:
    """Close pending work or request the executing Peer to stop running work."""
    async with job_uow() as uow:
      return await uow.jobs.abort(job_id)

  @classmethod
  async def check_abort_requests(cls) -> None:
    """Read this Peer's active abort requests in one batch."""
    if not cls._active:
      return
    async with job_uow() as uow:
      ids = await uow.jobs.abort_requests(tuple(cls._active))
    for job_id in ids:
      if job_id is None:
        continue
      execution = cls._active.get(job_id)
      if execution is not None:
        execution.cancel()

  @classmethod
  def start(cls) -> None:
    cls._accepting = True

  @classmethod
  async def shutdown(cls) -> None:
    """Stop admission and drain handlers before their Extension resources close."""
    cls._accepting = False
    executions = tuple(cls._active.values())
    for execution in executions:
      execution.cancel()
    await asyncio.gather(*(item.task for item in executions), return_exceptions=True)

  @classmethod
  async def _claim(cls, job_id: JobID) -> JobModel | None:
    if not cls._accepting:
      return None
    async with job_uow() as uow:
      return await uow.jobs.claim(job_id)

  @classmethod
  async def _close(cls, job: JobModel, status: JobStatus) -> bool:
    if not status.terminal:
      raise ValueError("Job may close only to a terminal status")
    execution = cls._active.get(job.id) if job.id is not None else None
    if execution is not None:
      execution.closing = True
    # Cleanup is independent of handler cancellation and cannot wait indefinitely.
    async with asyncio.timeout(10):
      async with job_uow() as uow:
        closed = await uow.jobs.close(job, status)
    if closed:
      emit_event(
        "job.closed", {"inkcre.job.id": job.id or 0, "inkcre.job.status": status.value}
      )
    return closed

  @classmethod
  async def run(cls, job_id: JobID) -> bool:
    """Check local eligibility, atomically claim, then execute one Job."""
    if not cls._accepting:
      return False
    candidate = await cls.get(job_id)
    if candidate is None or candidate.status != JobStatus.PENDING:
      return False
    try:
      prepared = await cls._prepare(candidate)
    except pydantic.ValidationError as error:
      # A broken persisted command must be visible as failed, not remain pending
      # forever. Unknown/unavailable handlers still leave work for another Peer.
      LOGGER.exception("Persisted Job parameters are invalid", extra={"job_id": job_id})
      claimed = await cls._claim(job_id)
      if claimed is None:
        return False
      claimed.state = {**claimed.state, "error": str(error)}
      await cls._close(claimed, JobStatus.FAILED)
      return True
    if prepared is None:
      return False

    claimed = await cls._claim(job_id)
    if claimed is None:
      return False
    handler, parameters = prepared
    task = asyncio.current_task()
    assert task is not None
    cls._active[job_id] = _Execution(task)
    with operation(
      "job.execute",
      attributes={"inkcre.job.id": job_id},
      context=Context(),
      links=submission_links(claimed.submission_traceparent, claimed.submission_tracestate),
    ) as observation:
      span = observation.span
      emit_event("job.started", {"inkcre.job.id": job_id})
      try:
        async with asyncio.timeout(claimed.timeout_seconds):
          await handler.handle(claimed, parameters)
      except asyncio.CancelledError:
        LOGGER.info("Job execution aborted", extra={"job_id": job_id})
        observation.outcome = "cancelled"
        span.set_attribute("inkcre.job.status", "aborted")
        await cls._close(claimed, JobStatus.ABORTED)
      except TimeoutError:
        LOGGER.warning("Job execution timed out", extra={"job_id": job_id})
        observation.outcome = "error"
        span.set_attribute("inkcre.job.status", "timed_out")
        await cls._close(claimed, JobStatus.TIMED_OUT)
      except Exception as error:
        LOGGER.exception("Job execution failed", extra={"job_id": job_id})
        observation.outcome = "error"
        span.set_attribute("error.type", type(error).__name__)
        span.set_attribute("inkcre.job.status", "failed")
        claimed.state = {**claimed.state, "error": str(error)}
        await cls._close(claimed, JobStatus.FAILED)
      else:
        span.set_attribute("inkcre.job.status", "finished")
        await cls._close(claimed, JobStatus.FINISHED)
      finally:
        cls._active.pop(job_id, None)
    return True

  @classmethod
  async def expire_overdue(cls) -> int:
    """Use database time to close abandoned overdue running Jobs."""
    async with job_uow() as uow:
      return await uow.jobs.expire_overdue()

  @classmethod
  async def check(cls) -> None:
    """Schedule locally eligible pending Jobs and converge running timeouts."""
    if not cls._accepting:
      return
    async with job_uow() as uow:
      pending = await uow.jobs.pending()

    for job in pending:
      if job.id is None or job.type not in cls._handlers:
        continue
      scheduler.add_job(
        func=with_trace_id(f"job.{job.id}", cls.run),
        args=[job.id],
        id=f"job.{job.id}",
        replace_existing=True,
        misfire_grace_time=None,
      )
    await cls.expire_overdue()
