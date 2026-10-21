"""Bounded, authenticated HTTP boundary for the exact capture submission capability."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.schemas.capture import CaptureResult

from .schema import MAX_REQUEST_BYTES, CaptureRequest
from .submit import CaptureConflict, InvalidCapture, submit_capture


class CaptureRoute(APIRoute):
  def get_route_handler(self):
    handler = super().get_route_handler()

    async def bounded(request: Request):
      received = 0

      async def receive():
        nonlocal received
        message = await request.receive()
        if message["type"] == "http.request":
          received += len(message.get("body", b""))
          if received > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="采集请求超过 48 MiB")
        return message

      try:
        return await handler(Request(request.scope, receive))
      except RequestValidationError as error:
        # FastAPI's default input echo would include whole images and captured private text.
        return JSONResponse(
          status_code=422,
          content={
            "detail": [
              {"loc": list(item["loc"]), "msg": f"字段格式不正确（{item['type']}）"}
              for item in error.errors()
            ]
          },
        )

    return bounded


router = APIRouter(route_class=CaptureRoute)


@router.post("/submit")
async def capture(body: CaptureRequest) -> CaptureResult:
  try:
    return await submit_capture(body)
  except CaptureConflict as error:
    raise HTTPException(status_code=409, detail=str(error)) from error
  except InvalidCapture as error:
    raise HTTPException(status_code=422, detail=str(error)) from error
