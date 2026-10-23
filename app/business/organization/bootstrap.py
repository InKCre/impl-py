"""Explicit loading of core Organization Behavior Resolvers."""

from app.business.info_base.resolver import ResolverManager


def register_core_organization_behaviors() -> None:
  from .duplicate_assertion import DuplicateAssertionBehaviorResolver
  from .evidence_stance import EvidenceStanceBehaviorResolver
  from .extraction import ExtractionBehaviorResolver
  from .referent_anchoring import ExistingReferentAnchoringBehaviorResolver
  from .refinement import RefinementBehaviorResolver
  from .rumination import RuminationBehaviorResolver
  from .supersession import SupersessionBehaviorResolver
  from .synthesis import SynthesisBehaviorResolver

  for resolver_class in (
    RuminationBehaviorResolver,
    SupersessionBehaviorResolver,
    RefinementBehaviorResolver,
    EvidenceStanceBehaviorResolver,
    SynthesisBehaviorResolver,
    ExistingReferentAnchoringBehaviorResolver,
    DuplicateAssertionBehaviorResolver,
    ExtractionBehaviorResolver,
  ):
    ResolverManager.register_resolver(resolver_class)
