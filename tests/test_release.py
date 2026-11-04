"""Release ownership follows the independently packaged Core source tree."""

from scripts import release


def test_sdk_only_change_requires_core_release_intent(monkeypatch):
  monkeypatch.setattr(
    release,
    "_changed_paths",
    lambda _base: ("inkcre_core/database_contract/constants.py",),
  )

  assert release.affected_projects("parent") == {"core"}
