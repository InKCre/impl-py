"""Built-in Agent Query Sink, durable execution, and result delivery."""

from __future__ import annotations

from app.contracts.jobs import AGENT_QUERY_JOB_TYPE as AGENT_QUERY_JOB_TYPE

from app.contracts.jobs import AgentQueryJobParameters as AgentQueryJobParameters

import typing

import fastapi
import pydantic
from starlette.routing import BaseRoute

from app.business.agent import AgentManager
from app.business.job import JobHandler, JobManager
from app.middleware import require_peer_jwt
from app.persistence.job.uow import JobUnitOfWork
from app.schemas.ai import (
  AssistantMessage,
  JSONValue,
  TextContentPart,
  ToolResultMessage,
  UserMessage,
)
from app.schemas.job import JobModel
from app.schemas.sink import SinkModel
from libs.obsrv.telemetry import emit_event

from .base import SinkBase
from .errors import SinkNotFoundError, SinkStateConflictError
from .main import SinkManager


AGENT_QUERY_SINK_TYPE = "core.agent-query.v1"
SUBMIT_QUERY_RESULT_TOOL = "submit_query_result"


class AgentQueryConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  agent: int


class AgentQueryReference(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: typing.Literal["block", "relation"]
  id: int


class AgentQueryResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  answer: str = pydantic.Field(min_length=1)
  references: tuple[AgentQueryReference, ...] = ()


class AgentQueryRequest(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  query: str = pydantic.Field(min_length=1)
  timeout_seconds: int | None = pydantic.Field(default=None, gt=0)


class AgentQueryResultMissingError(RuntimeError):
  """A normal Agent Turn ended without delivering its required result."""


@AgentManager.tool(
  SUBMIT_QUERY_RESULT_TOOL,
  description="Submit the answer to this information need and its supporting entities.",
)
async def submit_query_result(input: AgentQueryResult) -> JSONValue:
  return typing.cast(JSONValue, input.model_dump(mode="json"))


class AgentQuerySink(
  SinkBase[AgentQueryConfig],
  sink_type=AGENT_QUERY_SINK_TYPE,
  config_cls=AgentQueryConfig,
):
  """Answer information needs by composing registered info-base read tools."""

  def __init__(self, model: SinkModel) -> None:
    super().__init__(model)
    self._app: fastapi.FastAPI | None = None
    self._routes: tuple[BaseRoute, ...] = ()

  async def on_start(self, app: fastapi.FastAPI) -> None:
    sink_id = typing.cast(int, self.model.id)
    router = fastapi.APIRouter(dependencies=[fastapi.Depends(require_peer_jwt)])

    @router.post(
      f"/sinks/{sink_id}/query",
      status_code=fastapi.status.HTTP_202_ACCEPTED,
      name=f"agent_query_{sink_id}",
    )
    async def create_query(
      body: AgentQueryRequest,
      request: fastapi.Request,
      response: fastapi.Response,
      background: fastapi.BackgroundTasks,
    ) -> JobModel:
      job = await JobManager.create(
        AGENT_QUERY_JOB_TYPE,
        {"sink": sink_id, "query": body.query},
        body.timeout_seconds,
      )
      response.headers["Location"] = str(request.url_for("get_job", job_id=job.id))
      background.add_task(JobManager.notify_worker)
      return job

    # include_router adds app-owned objects (included-router wrappers in FastAPI
    # 0.139), not router.routes themselves. Capture those exact objects without
    # relying on their private type; this synchronous section cannot interleave.
    existing = {id(route) for route in app.router.routes}
    app.include_router(router)
    self._routes = tuple(route for route in app.router.routes if id(route) not in existing)
    # CLI schema discovery must reflect enable/disable, not a cached route set.
    app.openapi_schema = None
    self._app = app

  async def on_close(self) -> None:
    if self._app is not None and self._routes:
      # Identity removes only this instance's publication, never another route
      # sharing a path. MCPSink owns its directly appended Mount the same way.
      owned = {id(route) for route in self._routes}
      self._app.router.routes[:] = [
        route for route in self._app.router.routes if id(route) not in owned
      ]
      self._app.openapi_schema = None
    self._routes = ()
    self._app = None

  async def can_execute(self) -> bool:
    return await AgentManager.can_execute(self.config.agent, "text")

  async def execute(self, job: JobModel, query: str) -> None:
    thread = await AgentManager.run(
      self.config.agent,
      UserMessage(content=(TextContentPart(text=query),)),
    )
    turn = thread.current_turn
    if turn is None:  # pragma: no cover - AgentManager.run invariant
      raise RuntimeError("Agent Query Thread has no active Turn")
    try:
      termination = await turn
    finally:
      result = _last_query_result(thread.messages)
      if result is not None:
        job.state = {**job.state, "result": result}
        # Emit only the closed submission selected for this Job, not every attempt.
        references = typing.cast(list[dict[str, JSONValue]], result["references"])
        reported = [
          reference
          for reference in references[:128]
          if type(reference["id"]) is int and 0 < typing.cast(int, reference["id"]) < 2**63
        ]
        emit_event(
          "inkcre.agent.query.result",
          {
            "inkcre.job.id": typing.cast(int, job.id),
            "gen_ai.conversation.id": str(thread.id),
            "inkcre.entity.block_ids": tuple(
              typing.cast(int, ref["id"]) for ref in reported if ref["type"] == "block"
            ),
            "inkcre.entity.relation_ids": tuple(
              typing.cast(int, ref["id"]) for ref in reported if ref["type"] == "relation"
            ),
            "inkcre.agent.reference_count": len(references),
            "inkcre.agent.unreported_reference_count": len(references) - len(reported),
          },
        )
    job.state = {**job.state, "termination": termination.value}
    if result is None:
      raise AgentQueryResultMissingError(
        "Agent Query completed without submit_query_result"
      )


class AgentQueryJobHandler(
  JobHandler[AgentQueryJobParameters],
  job_type=AGENT_QUERY_JOB_TYPE,
):
  @classmethod
  async def normalize_parameters(
    cls,
    parameters: dict[str, typing.Any],
    uow: JobUnitOfWork,
  ) -> dict[str, typing.Any]:
    normalized = await super().normalize_parameters(parameters, uow)
    sink = await SinkManager.get(normalized["sink"])
    if sink.type != AGENT_QUERY_SINK_TYPE:
      raise SinkStateConflictError(
        f"Sink {sink.id} is not an {AGENT_QUERY_SINK_TYPE} instance"
      )
    return normalized

  @classmethod
  async def can_handle(cls, parameters: AgentQueryJobParameters) -> bool:
    sink = SinkManager.get_running(parameters.sink)
    return isinstance(sink, AgentQuerySink) and await sink.can_execute()

  @classmethod
  async def handle(cls, job: JobModel, parameters: AgentQueryJobParameters) -> None:
    sink = SinkManager.get_running(parameters.sink)
    if not isinstance(sink, AgentQuerySink):
      raise SinkNotFoundError(f"Agent Query Sink {parameters.sink} is not running")
    await sink.execute(job, parameters.query)


def _last_query_result(
  messages: typing.Iterable[typing.Any],
) -> dict[str, JSONValue] | None:
  """Read the last successful submission from closed Assistant/ToolResult pairs.

  Thread._execute_turn atomically appends the Assistant and its complete results
  as adjacent messages, in ToolCall order. The last successful submit in that
  order wins; a later failed submit never replaces an earlier successful result.
  """
  sequence = tuple(messages)
  selected: dict[str, JSONValue] | None = None
  for index, message in enumerate(sequence[:-1]):
    if not isinstance(message, AssistantMessage) or not message.tool_calls:
      continue
    results = sequence[index + 1]
    if not isinstance(results, ToolResultMessage):
      continue
    submitted = {
      call.id for call in message.tool_calls if call.tool == SUBMIT_QUERY_RESULT_TOOL
    }
    for result in results.results:
      if result.tool_call_id in submitted and not result.is_error:
        selected = typing.cast(dict[str, JSONValue], result.content)
  return selected
