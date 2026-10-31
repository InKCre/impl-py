"""Exact automatic Organization commands hosted by Jobs."""

from app.business.job import JobHandler
from app.business.organization_media import (
  MEDIA_INTERPRETATION_JOB_TYPE,
  can_handle_media_interpretation,
  interpret_missing_media,
)
from app.schemas.job import JobModel
from app.schemas.organization import MediaInterpretationJobParameters


class MediaInterpretationJobHandler(
  JobHandler[MediaInterpretationJobParameters],
  job_type=MEDIA_INTERPRETATION_JOB_TYPE,
):
  @classmethod
  async def can_handle(cls, parameters: MediaInterpretationJobParameters) -> bool:
    del parameters
    return await can_handle_media_interpretation()

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: MediaInterpretationJobParameters,
  ) -> None:
    del parameters
    report = await interpret_missing_media()
    job.state = report.model_dump(mode="json")
