"""Session-bound receipt serialization; lifecycle belongs to the capture UoW."""

from hashlib import sha256
from uuid import UUID

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.capture import CaptureReceipt, CaptureReceiptModel


class CaptureReceiptRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def lock(self, submission_id: UUID) -> None:
    key = int.from_bytes(
      sha256(b"inkcre.capture.submit.v1:" + submission_id.bytes).digest()[:8],
      "big",
      signed=True,
    )
    await self._session.execute(
      sqlalchemy.text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
    )

  async def get(self, submission_id: UUID) -> CaptureReceipt | None:
    row = await self._session.get(CaptureReceiptModel, submission_id)
    return CaptureReceipt.model_validate(row) if row is not None else None

  async def create(
    self, submission_id: UUID, payload_digest: str, root_block_id: int
  ) -> CaptureReceipt:
    row = CaptureReceiptModel(
      submission_id=submission_id,
      payload_digest=payload_digest,
      root_block_id=root_block_id,
    )
    self._session.add(row)
    await self._session.flush()
    return CaptureReceipt.model_validate(row)
