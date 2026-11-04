"""Deployment-scoped config HTTP resource."""

__all__ = ["ROUTER"]

import typing

import fastapi

from inkcre_core.business.deployment_config import (
  DeploymentConfigManager,
  DeploymentConfigService,
  DeploymentConfigNotFoundError,
  UnknownDeploymentConfigSchemaError,
)
from inkcre_core.schemas.deployment_config import (
  DeploymentConfigKey,
  DeploymentConfigReplaceForm,
  DeploymentConfigView,
)
from .validation import request_input


ROUTER = fastapi.APIRouter(tags=["config"])


def _unusable_config(error: Exception) -> typing.NoReturn:
  raise fastapi.HTTPException(
    status_code=fastapi.status.HTTP_409_CONFLICT,
    detail=str(error),
  ) from error


@ROUTER.get("/configs")
async def list_configs(
  limit: int | None = fastapi.Query(None, gt=0), cursor: str | None = None
) -> dict[str, typing.Any]:
  rows, next_cursor = await DeploymentConfigService.list_configs(limit=limit, cursor=cursor)
  return {"configs": rows, "next_cursor": next_cursor}


@ROUTER.get("/config-schemas")
def list_config_schemas(
  limit: int | None = fastapi.Query(None, gt=0), cursor: str | None = None
) -> dict[str, typing.Any]:
  rows, next_cursor = DeploymentConfigManager.list_schemas(limit=limit, cursor=cursor)
  return {"schemas": rows, "next_cursor": next_cursor}


@ROUTER.get("/config-schemas/{schema_id}")
def get_config_schema(schema_id: str) -> dict:
  try:
    return DeploymentConfigManager.get_schema(schema_id)
  except UnknownDeploymentConfigSchemaError as error:
    raise fastapi.HTTPException(404, str(error)) from error


@ROUTER.get("/configs/{key}", response_model_by_alias=True)
async def get_config(key: DeploymentConfigKey) -> DeploymentConfigView:
  config = await DeploymentConfigService.read(key)
  if config is None:
    raise fastapi.HTTPException(
      status_code=fastapi.status.HTTP_404_NOT_FOUND,
      detail=f"Deployment config {key!r} not found",
    )
  return config


@ROUTER.put("/configs/{key}", response_model_by_alias=True)
async def replace_config(
  key: DeploymentConfigKey,
  body: DeploymentConfigReplaceForm,
  response: fastapi.Response,
) -> DeploymentConfigView:
  try:
    with request_input("value"):
      result, created = await DeploymentConfigService.replace_with_status(
        key, body.schema_id, body.value
      )
    response.status_code = 201 if created else 200
    return result
  except UnknownDeploymentConfigSchemaError as error:
    raise fastapi.HTTPException(
      status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
      detail=str(error),
    ) from error


@ROUTER.patch("/configs/{key}", response_model_by_alias=True)
async def patch_config(
  key: DeploymentConfigKey,
  body: dict[str, typing.Any] = fastapi.Body(...),
) -> DeploymentConfigView:
  try:
    with request_input():
      return await DeploymentConfigService.patch(key, body)
  except DeploymentConfigNotFoundError as error:
    raise fastapi.HTTPException(
      status_code=fastapi.status.HTTP_404_NOT_FOUND,
      detail=str(error),
    ) from error
  except UnknownDeploymentConfigSchemaError as error:
    _unusable_config(error)


@ROUTER.delete("/configs/{key}", status_code=204)
async def delete_config(key: str) -> None:
  if not await DeploymentConfigService.delete(key):
    raise fastapi.HTTPException(404, f"Deployment config {key!r} not found")
