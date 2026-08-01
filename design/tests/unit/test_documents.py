from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from mcp_server_design.documents import DesignStore, Settings


def git_init(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_pair_lifecycle_tracks_dirty_working_tree_snapshots(tmp_path: Path) -> None:
    plan = tmp_path / "plan"
    repo = tmp_path / "service"
    git_init(repo)
    (repo / "service.py").write_text("VERSION = 1\n", encoding="utf-8")
    store = DesignStore(Settings(plan))

    built = store.build_design(repos=[str(repo)], title="Service change", domains=["runtime"])
    indexed = store.index_design(built["design_path"])
    verified = store.verify_design(built["design_path"])

    assert indexed == {**indexed, "requirement_count": 1, "changed": True}
    assert verified["snapshot_differences"] == []
    (repo / "service.py").write_text("VERSION = 2\n", encoding="utf-8")
    assert store.verify_design(built["design_path"])["snapshot_differences"] == [str(repo)]

    captured = store.capture_implementation(built["design_path"])
    assert captured["status"] == "implemented"
    assert store.verify_design(built["design_path"])["snapshot_differences"] == []


def test_relation_state_is_enforced(tmp_path: Path) -> None:
    repo = tmp_path / "service"
    git_init(repo)
    store = DesignStore(Settings(tmp_path / "plan"))
    first = store.build_design(repos=[str(repo)], title="First")
    store.index_design(first["design_path"])

    with pytest.raises(ValueError, match="implemented"):
        store.build_design(extend=first["design_path"], title="Too early")

    replacement = store.build_design(supersede=first["design_path"], title="Replacement")
    assert replacement["relation"]["kind"] == "supersedes"
