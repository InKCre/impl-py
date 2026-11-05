"""Persisted Agent definition loading and exact Tool registry binding."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import typing

import pydantic

from inkcre_core.business.ai import AIExecutionRequirement, AIManager
from inkcre_core.schemas import AgentDefinitionModel
from inkcre_core.schemas.agent import AgentID, AgentForm, AgentUpdateForm
from inkcre_core.schemas.ai import FunctionTool, SystemMessage, UserMessage

from inkcre_core.persistence.agent.uow import agent_uow

from .contracts import (
  AgentNotFoundError,
  AgentToolBindingError,
  AgentToolHandler,
  AgentToolInputModelFactory,
  BoundAgentTool,
  DuplicateAgentToolRegistrationError,
  MissingAgentToolError,
)
from .persistence import (
  InMemoryThreadPersistenceBackend,
  ThreadPersistenceBackend,
  ThreadState,
)
from .thread import Thread
from .debug import trace


HandlerT = typing.TypeVar("HandlerT", bound=typing.Callable[..., typing.Any])


@dataclass(frozen=True)
class _ToolRegistration:
  handler: AgentToolHandler
  original_handler: typing.Callable[..., typing.Any]
  description: str
  input_model: type[pydantic.BaseModel]
  input_model_factory: AgentToolInputModelFactory | None

  def bind(self, tool_id: str) -> BoundAgentTool:
    input_model = (
      self.input_model_factory()
      if self.input_model_factory is not None
      else self.input_model
    )
    if not isinstance(input_model, type) or not issubclass(input_model, pydantic.BaseModel):
      raise AgentToolBindingError(
        f"Agent Tool {tool_id!r} did not produce a Pydantic input model"
      )
    return BoundAgentTool(
      definition=FunctionTool(
        id=tool_id,
        description=self.description,
        input_schema=input_model.model_json_schema(),
      ),
      input_model=input_model,
      handler=self.handler,
    )


class AgentManager:
  """Agent-definition execution and peer-local exact Tool registry."""

  _TOOLS: dict[str, _ToolRegistration] = {}
  _persistence: ThreadPersistenceBackend = InMemoryThreadPersistenceBackend()

  @classmethod
  async def get_definition(cls, agent_id: AgentID) -> AgentDefinitionModel | None:
    async with agent_uow() as agents:
      return await agents.get(agent_id)

  @classmethod
  async def list_definitions(
    cls, *, limit: int | None = None, cursor: int | None = None
  ) -> tuple[list[AgentDefinitionModel], int | None]:
    async with agent_uow() as agents:
      return await agents.list(limit=limit, cursor=cursor)

  @classmethod
  async def create_definition(cls, form: AgentForm) -> AgentDefinitionModel:
    async with agent_uow() as agents:
      record = await agents.create(form)
    return record

  @classmethod
  async def update_definition(
    cls, agent_id: AgentID, form: AgentUpdateForm
  ) -> AgentDefinitionModel:
    async with agent_uow() as agents:
      record = await agents.get(agent_id, for_update=True)
      if record is None:
        raise AgentNotFoundError(f"Agent {agent_id} does not exist")
      changes = form.model_dump(exclude_unset=True)
      candidate = AgentForm.model_validate(
        {**{field: getattr(record, field) for field in AgentForm.model_fields}, **changes}
      )
      for field in changes:
        setattr(record, field, getattr(candidate, field))
      await agents.save(record)
    return record

  @classmethod
  async def delete_definition(cls, agent_id: AgentID) -> bool:
    async with agent_uow() as agents:
      record = await agents.get(agent_id)
      if record is None:
        return False
      await agents.delete(record)
    return True

  @classmethod
  def list_tools(
    cls, *, limit: int | None = None, cursor: str | None = None
  ) -> tuple[list[dict[str, str]], str | None]:
    ids = sorted(key for key in cls._TOOLS if cursor is None or key > cursor)
    more = limit is not None and len(ids) > limit
    ids = ids[:limit]
    return [{"id": key, "description": cls._TOOLS[key].description} for key in ids], ids[
      -1
    ] if more else None

  @classmethod
  def get_tool(cls, tool_id: str) -> FunctionTool:
    registration = cls._TOOLS.get(tool_id)
    if registration is None:
      raise MissingAgentToolError(f"Agent Tool {tool_id!r} is not registered")
    return registration.bind(tool_id).definition

  @classmethod
  async def can_execute(cls, agent_id: AgentID, input_modality: str) -> bool:
    """Return static local eligibility for one Agent and canonical input modality."""
    definition = await cls.get_definition(agent_id)
    if definition is None:
      return False
    try:
      cls._bind_tools(definition.tools)
    except MissingAgentToolError:
      return False
    requires_tools = bool(definition.tools) or definition.tool_choice is not None
    return await AIManager.can_execute(
      definition.model,
      AIExecutionRequirement(
        capability="chat",
        input_modalities=frozenset({"text", input_modality}),
        output_modalities=frozenset({"text"}),
        features=frozenset({"tool_calling"}) if requires_tools else frozenset(),
        tool_choice=definition.tool_choice,
      ),
    )

  @classmethod
  def tool(
    cls,
    tool_id: str,
    *,
    description: str,
    input_model_factory: AgentToolInputModelFactory | None = None,
  ) -> typing.Callable[[HandlerT], HandlerT]:
    """Register one function as a typed Agent Tool and return it unchanged."""
    if not tool_id:
      raise AgentToolBindingError("Agent Tool ID must not be empty")

    def decorator(handler: HandlerT) -> HandlerT:
      signature = inspect.signature(handler)
      parameters = tuple(signature.parameters.values())
      if len(parameters) != 1:
        raise AgentToolBindingError(
          f"Agent Tool {tool_id!r} handler must accept exactly one input model"
        )
      hints = typing.get_type_hints(handler)
      annotation = hints.get(parameters[0].name)
      if not isinstance(annotation, type) or not issubclass(annotation, pydantic.BaseModel):
        raise AgentToolBindingError(
          f"Agent Tool {tool_id!r} input must be annotated with a Pydantic model"
        )

      existing = cls._TOOLS.get(tool_id)
      if existing is not None:
        if (
          existing.original_handler is handler
          and existing.description == description
          and existing.input_model_factory is input_model_factory
        ):
          return handler
        raise DuplicateAgentToolRegistrationError(
          f"Agent Tool {tool_id!r} is already registered"
        )

      cls._TOOLS[tool_id] = _ToolRegistration(
        handler=typing.cast(AgentToolHandler, handler),
        original_handler=handler,
        description=description,
        input_model=annotation,
        input_model_factory=input_model_factory,
      )
      return handler

    return decorator

  @classmethod
  async def run(cls, agent_id: AgentID, initial_message: UserMessage) -> Thread:
    """Create one active Thread from a persisted Agent definition snapshot."""
    definition = await cls.get_definition(agent_id)
    if definition is None:
      raise AgentNotFoundError(f"Agent {agent_id} does not exist")

    bound_tools = cls._bind_tools(definition.tools)
    state = ThreadState(
      model=definition.model,
      tools=tuple(tool.definition for tool in bound_tools),
      tool_choice=definition.tool_choice,
      max_model_calls_per_turn=definition.max_model_calls_per_turn,
      messages=(SystemMessage(content=definition.system_prompt),),
    )
    thread_id, persisted = await cls._persistence.create(state)
    await trace(
      "agent.thread.created",
      thread_id,
      agent_id=agent_id,
      agent_name=definition.name,
      state=persisted,
    )
    thread = Thread(thread_id, persisted, cls._persistence, bound_tools)
    thread.start_turn(initial_message)
    return thread

  @classmethod
  def _bind_tools(cls, tool_ids: tuple[str, ...]) -> tuple[BoundAgentTool, ...]:
    bound: list[BoundAgentTool] = []
    for tool_id in tool_ids:
      registration = cls._TOOLS.get(tool_id)
      if registration is None:
        raise MissingAgentToolError(
          f"Agent definition references unavailable Tool {tool_id!r}"
        )
      bound.append(registration.bind(tool_id))
    return tuple(bound)
