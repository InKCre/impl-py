"""Exact automatic Organization commands hosted by Jobs."""

from inkcre_core.business.job import JobHandler
from inkcre_core.business.organization_media import (
  MEDIA_INTERPRETATION_JOB_TYPE,
  can_handle_media_interpretation,
  interpret_missing_media,
)
from inkcre_core.schemas.job import JobModel
from inkcre_core.schemas.organization import MediaInterpretationJobParameters


class MediaInterpretationJobHandler(
  JobHandler[MediaInterpretationJobParameters],
  job_type=MEDIA_INTERPRETATION_JOB_TYPE,
  description="Interpret missing image, audio, and video Blocks through configured Agents.",
  parameters_model=MediaInterpretationJobParameters,
  default_timeout_seconds=1800,
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
