from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_every_local_server_is_an_installable_pinned_workspace_package() -> None:
    workspace = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages = [ROOT / member for member in workspace["tool"]["uv"]["workspace"]["members"]]

    assert {path.name for path in packages} == {"design"}
    for package in packages:
        manifest = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
        assert "mcp==2.0.0" in manifest["project"]["dependencies"]
        assert manifest["project"]["scripts"]
        assert (package / "src").is_dir()
        assert (package / "tests" / "component").is_dir()
