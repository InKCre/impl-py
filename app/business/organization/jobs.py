"""Independent automatic Organization Jobs and explicit focal requests."""

from app.business.job import JobHandler
from app.schemas.job import JobModel
from app.schemas.organization import RuminationRequest
from app.schemas.organization_behavior import AutomaticOrganizationJobParameters
from app.schemas.organization_extraction import ExtractionRequest

from .duplicate_assertion import DuplicateAssertionBehaviorResolver
from .evidence_stance import EvidenceStanceBehaviorResolver
from .extraction import ExtractionBehaviorResolver
from .referent_anchoring import ExistingReferentAnchoringBehaviorResolver
from .refinement import RefinementBehaviorResolver
from .rumination import RuminationBehaviorResolver
from .supersession import SupersessionBehaviorResolver
from .synthesis import SynthesisBehaviorResolver


RUMINATION_JOB_TYPE = "core.organization.rumination.automatic.v1"
EXPLICIT_RUMINATION_JOB_TYPE = "core.organization.rumination.explicit.v1"
SUPERSESSION_JOB_TYPE = "core.organization.supersession.automatic.v1"
REFINEMENT_JOB_TYPE = "core.organization.refinement.automatic.v1"
EVIDENCE_STANCE_JOB_TYPE = "core.organization.evidence-stance.automatic.v1"
SYNTHESIS_JOB_TYPE = "core.organization.synthesis.automatic.v1"
REFERENT_ANCHORING_JOB_TYPE = "core.organization.existing-referent-anchoring.automatic.v1"
DUPLICATE_ASSERTION_JOB_TYPE = "core.organization.duplicate-assertion.automatic.v1"
EXTRACTION_JOB_TYPE = "core.organization.extraction.automatic.v1"
EXPLICIT_EXTRACTION_JOB_TYPE = "core.organization.extraction.explicit.v1"


class ExplicitExtractionJobHandler(
  JobHandler[ExtractionRequest],
  job_type=EXPLICIT_EXTRACTION_JOB_TYPE,
  description="Extract reusable information from one explicitly selected material.",
  parameters_model=ExtractionRequest,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: ExtractionRequest) -> bool:
    del parameters
    return await ExtractionBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(cls, job: JobModel, parameters: ExtractionRequest) -> None:
    del job
    await ExtractionBehaviorResolver.extract_local(parameters.block_id)


class ExtractionJobHandler(
  JobHandler[AutomaticOrganizationJobParameters],
  job_type=EXTRACTION_JOB_TYPE,
  description="Extract reusable information from bounded automatic material candidates.",
  parameters_model=AutomaticOrganizationJobParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: AutomaticOrganizationJobParameters) -> bool:
    del parameters
    return await ExtractionBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(
    cls, job: JobModel, parameters: AutomaticOrganizationJobParameters
  ) -> None:
    del job
    await ExtractionBehaviorResolver.run_automatic(parameters.max_seeds)


class ExplicitRuminationJobHandler(
  JobHandler[RuminationRequest],
  job_type=EXPLICIT_RUMINATION_JOB_TYPE,
  description="Reconsider one explicitly selected Block through rumination.",
  parameters_model=RuminationRequest,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: RuminationRequest) -> bool:
    del parameters
    return await RuminationBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(cls, job: JobModel, parameters: RuminationRequest) -> None:
    del job
    # A claimed Job executes here; it must not delegate another execution.
    await RuminationBehaviorResolver.ruminate_local(parameters.block)


class RuminationJobHandler(
  JobHandler[AutomaticOrganizationJobParameters],
  job_type=RUMINATION_JOB_TYPE,
  description="Automatically reconsider bounded information seeds through rumination.",
  parameters_model=AutomaticOrganizationJobParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: AutomaticOrganizationJobParameters) -> bool:
    del parameters
    return await RuminationBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: AutomaticOrganizationJobParameters,
  ) -> None:
    del job
    await RuminationBehaviorResolver.run_automatic(parameters.max_seeds)


class SupersessionJobHandler(
  JobHandler[AutomaticOrganizationJobParameters],
  job_type=SUPERSESSION_JOB_TYPE,
  description="Automatically judge bounded scoped-supersession candidates.",
  parameters_model=AutomaticOrganizationJobParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: AutomaticOrganizationJobParameters) -> bool:
    del parameters
    return await SupersessionBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: AutomaticOrganizationJobParameters,
  ) -> None:
    del job
    await SupersessionBehaviorResolver.run_automatic(parameters.max_seeds)


class RefinementJobHandler(
  JobHandler[AutomaticOrganizationJobParameters],
  job_type=REFINEMENT_JOB_TYPE,
  description="Automatically judge bounded non-dominating refinement candidates.",
  parameters_model=AutomaticOrganizationJobParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: AutomaticOrganizationJobParameters) -> bool:
    del parameters
    return await RefinementBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: AutomaticOrganizationJobParameters,
  ) -> None:
    del job
    await RefinementBehaviorResolver.run_automatic(parameters.max_seeds)


class EvidenceStanceJobHandler(
  JobHandler[AutomaticOrganizationJobParameters],
  job_type=EVIDENCE_STANCE_JOB_TYPE,
  description="Automatically judge bounded attributable evidence stances.",
  parameters_model=AutomaticOrganizationJobParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: AutomaticOrganizationJobParameters) -> bool:
    del parameters
    return await EvidenceStanceBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: AutomaticOrganizationJobParameters,
  ) -> None:
    del job
    await EvidenceStanceBehaviorResolver.run_automatic(parameters.max_seeds)


class SynthesisJobHandler(
  JobHandler[AutomaticOrganizationJobParameters],
  job_type=SYNTHESIS_JOB_TYPE,
  description="Automatically create bounded provenance-preserving syntheses.",
  parameters_model=AutomaticOrganizationJobParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: AutomaticOrganizationJobParameters) -> bool:
    del parameters
    return await SynthesisBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: AutomaticOrganizationJobParameters,
  ) -> None:
    del job
    await SynthesisBehaviorResolver.run_automatic(parameters.max_seeds)


class ExistingReferentAnchoringJobHandler(
  JobHandler[AutomaticOrganizationJobParameters],
  job_type=REFERENT_ANCHORING_JOB_TYPE,
  description="Automatically anchor bounded mentions to existing referents.",
  parameters_model=AutomaticOrganizationJobParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: AutomaticOrganizationJobParameters) -> bool:
    del parameters
    return await ExistingReferentAnchoringBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: AutomaticOrganizationJobParameters,
  ) -> None:
    del job
    await ExistingReferentAnchoringBehaviorResolver.run_automatic(parameters.max_seeds)


class DuplicateAssertionJobHandler(
  JobHandler[AutomaticOrganizationJobParameters],
  job_type=DUPLICATE_ASSERTION_JOB_TYPE,
  description="Automatically judge bounded provenance-aware duplicate assertions.",
  parameters_model=AutomaticOrganizationJobParameters,
  default_timeout_seconds=1800,
):
  @classmethod
  async def can_handle(cls, parameters: AutomaticOrganizationJobParameters) -> bool:
    del parameters
    return await DuplicateAssertionBehaviorResolver.can_run_automatic()

  @classmethod
  async def handle(
    cls,
    job: JobModel,
    parameters: AutomaticOrganizationJobParameters,
  ) -> None:
    del job
    await DuplicateAssertionBehaviorResolver.run_automatic(parameters.max_seeds)
