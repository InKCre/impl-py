import json
import sys

from scripts.extension_distribution import main


def test_metadata_targets_only_the_exact_python_distribution(tmp_path, monkeypatch, capsys):
  project = tmp_path / "extension"
  project.mkdir()
  (project / "pyproject.toml").write_text(
    """
[project]
name = "inkcre-example"
version = "1.2.3"
dependencies = []

[project.entry-points."inkcre.core.extensions"]
example = "extensions.example:ExampleExtension"

[tool.inkcre-extension]
name = "inkcre/example"
nickname = "Example"
host-sdk = "core-py"
host-sdk-version = ">=0.3.0 <0.4.0"
""",
    encoding="utf-8",
  )

  monkeypatch.setattr(
    sys,
    "argv",
    [
      "extension_distribution.py",
      "metadata",
      "--project",
      str(project),
      "--source-repository",
      "https://github.com/InKCre/impl-py",
      "--source-revision",
      "a" * 40,
      "--build-id",
      "12345",
    ],
  )
  assert main() == 0
  metadata = json.loads(capsys.readouterr().out)
  assert metadata == {
    "coordinate": "inkcre/example",
    "distribution": "python",
    "version": "1.2.3",
    "project": "inkcre-example",
    "prepare": {
      "nickname": "Example",
      "version": "1.2.3",
      "python": {
        "project": "inkcre-example",
        "host_sdk": "core-py",
        "host_sdk_version": ">=0.3.0 <0.4.0",
        "entry_point": {
          "group": "inkcre.core.extensions",
          "name": "example",
          "object": "extensions.example:ExampleExtension",
        },
        "source_repository": "https://github.com/InKCre/impl-py",
        "source_revision": "a" * 40,
        "build_id": "12345",
      },
    },
  }
