"""Independent automatic Organization routes and explicit rumination Jobs."""

from app.contracts.jobs import (
  RUMINATION_JOB_TYPE as RUMINATION_JOB_TYPE,
  EXPLICIT_RUMINATION_JOB_TYPE as EXPLICIT_RUMINATION_JOB_TYPE,
  SUPERSESSION_JOB_TYPE as SUPERSESSION_JOB_TYPE,
  REFINEMENT_JOB_TYPE as REFINEMENT_JOB_TYPE,
  EVIDENCE_STANCE_JOB_TYPE as EVIDENCE_STANCE_JOB_TYPE,
  SYNTHESIS_JOB_TYPE as SYNTHESIS_JOB_TYPE,
  REFERENT_ANCHORING_JOB_TYPE as REFERENT_ANCHORING_JOB_TYPE,
  DUPLICATE_ASSERTION_JOB_TYPE as DUPLICATE_ASSERTION_JOB_TYPE,
)

from app.business.job import JobHandler
from app.schemas.job import JobModel
from app.schemas.organization import RuminationRequest
from app.schemas.organization_behavior import AutomaticOrganizationJobParameters

from .duplicate_assertion import DuplicateAssertionBehaviorResolver
from .evidence_stance import EvidenceStanceBehaviorResolver
from .referent_anchoring import ExistingReferentAnchoringBehaviorResolver
from .refinement import RefinementBehaviorResolver
from .rumination import RuminationBehaviorResolver
from .supersession import SupersessionBehaviorResolver
from .synthesis import SynthesisBehaviorResolver


class ExplicitRuminationJobHandler(
  JobHandler[RuminationRequest],
  job_type=EXPLICIT_RUMINATION_JOB_TYPE,
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
