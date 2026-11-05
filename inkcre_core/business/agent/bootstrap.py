"""Explicit registration entrypoint for core-owned Agent Tool controllers."""


def register_core_agent_tools() -> None:
  """Import every core Tool owner, independent of HTTP Sink delivery.

  Decorators register Tools when their modules load; Python's import cache makes
  repeated bootstrap calls harmless. Entry points must call this explicitly,
  rather than rely on routes or package re-exports importing the owners first.
  """
  from inkcre_core.business.graph_navigation_retrieval import tools as _graph_tools
  from inkcre_core.business.info_base import tools as _info_base_tools
  from inkcre_core.business.info_base.resolver import tools as _resolver_tools
  from inkcre_core.business.organization import tools as _organization_tools

  del _graph_tools, _info_base_tools, _resolver_tools, _organization_tools
