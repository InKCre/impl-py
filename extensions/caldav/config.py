"""Deployment-scoped DAV credentials; null revokes protocol access."""

from pydantic import BaseModel, ConfigDict, Field


class CalDAVConfig(BaseModel):
  model_config = ConfigDict(extra="forbid")

  password: str | None = Field(
    default=None,
    min_length=32,
    max_length=128,
    json_schema_extra={"format": "password"},
    description="DAV 专用密码（至少 32 字符）。用户名固定为 inkcre；清空密码撤销访问。",
  )
