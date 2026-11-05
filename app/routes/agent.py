"""Ordinary Agent definition management and local Tool discovery."""

import typing

import fastapi

from inkcre_core.business.agent.main import AgentManager
from inkcre_core.business.agent.contracts import AgentNotFoundError, MissingAgentToolError
from inkcre_core.schemas.agent import AgentDefinitionModel, AgentForm, AgentUpdateForm
from inkcre_core.schemas.ai import FunctionTool

from .validation import database_write, request_input


ROUTER = fastapi.APIRouter(tags=["agent"])


@ROUTER.get("/agents")
async def list_agents(
  limit: int | None = fastapi.Query(None, gt=0), cursor: int | None = None
) -> dict[str, typing.Any]:
  rows, next_cursor = await AgentManager.list_definitions(limit=limit, cursor=cursor)
  return {"agents": rows, "next_cursor": next_cursor}


@ROUTER.get("/agents/{agent_id}")
async def get_agent(agent_id: int) -> AgentDefinitionModel:
  result = await AgentManager.get_definition(agent_id)
  if result is None:
    raise fastapi.HTTPException(404, f"Agent {agent_id} not found")
  return result


@ROUTER.post("/agents", status_code=201)
async def create_agent(
  body: AgentForm, request: fastapi.Request, response: fastapi.Response
) -> AgentDefinitionModel:
  with database_write():
    result = await AgentManager.create_definition(body)
  response.headers["Location"] = str(request.url_for("get_agent", agent_id=result.id))
  return result


@ROUTER.patch("/agents/{agent_id}")
async def update_agent(agent_id: int, body: AgentUpdateForm) -> AgentDefinitionModel:
  try:
    with database_write(), request_input():
      return await AgentManager.update_definition(agent_id, body)
  except AgentNotFoundError as error:
    raise fastapi.HTTPException(404, str(error)) from error


@ROUTER.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: int) -> None:
  if not await AgentManager.delete_definition(agent_id):
    raise fastapi.HTTPException(404, f"Agent {agent_id} not found")


@ROUTER.get("/agent-tools")
def list_agent_tools(
  limit: int | None = fastapi.Query(None, gt=0), cursor: str | None = None
) -> dict[str, typing.Any]:
  tools, next_cursor = AgentManager.list_tools(limit=limit, cursor=cursor)
  return {"tools": tools, "next_cursor": next_cursor}


@ROUTER.get("/agent-tools/{tool_id}")
def get_agent_tool(tool_id: str) -> FunctionTool:
  try:
    return AgentManager.get_tool(tool_id)
  except MissingAgentToolError as error:
    raise fastapi.HTTPException(404, str(error)) from error
