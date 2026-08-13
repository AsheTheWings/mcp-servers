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
    design_doc = built["design_filename"]
    indexed = store.index_design(design_doc)
    verified = store.verify_design(design_doc)

    assert indexed == {**indexed, "requirement_count": 1, "changed": True}
    repository = verified["repositories"][str(repo)]
    assert repository["current_changes_from_design"]["files_changed"] == 0
    assert repository["implementation_changes_from_design"] is None
    assert repository["current_matches_implementation"] is None
    (repo / "service.py").write_text("VERSION = 2\n", encoding="utf-8")
    active = store.verify_design(design_doc)["repositories"][str(repo)]
    assert active["current_changes_from_design"] == {
        "files_changed": 1,
        "insertions": 1,
        "deletions": 1,
        "binary_files": 0,
        "files": [
            {
                "path": "service.py",
                "status": "modified",
                "insertions": 1,
                "deletions": 1,
                "binary": False,
            }
        ],
    }

    captured = store.capture_implementation(design_doc)
    assert captured["status"] == "implemented"
    assert "design_path" not in captured
    assert captured["design_filename"] == design_doc
    implemented = store.verify_design(design_doc)["repositories"][str(repo)]
    assert implemented["current_changes_from_design"] is None
    assert (
        implemented["implementation_changes_from_design"] == active["current_changes_from_design"]
    )
    assert implemented["current_matches_implementation"] is True

    (repo / "service.py").write_text("VERSION = 3\n", encoding="utf-8")
    drifted = store.verify_design(design_doc)["repositories"][str(repo)]
    assert drifted["implementation_changes_from_design"] == active["current_changes_from_design"]
    assert drifted["current_matches_implementation"] is False


def test_build_can_scaffold_a_standalone_design(tmp_path: Path) -> None:
    plan = tmp_path / "plan"
    repo = tmp_path / "service"
    git_init(repo)
    (repo / "service.py").write_text("VERSION = 1\n", encoding="utf-8")
    store = DesignStore(Settings(plan))

    standalone = store.build_design(
        repos=[str(repo)],
        title="Standalone architecture",
        domains=["runtime"],
        features=["Design-only planning"],
        include_requirements=False,
    )

    design_path = plan / "design" / standalone["design_filename"]
    document = store.read_document(design_path, "design document")
    assert standalone["requirements_created"] is False
    assert standalone["requirements_path"] is None
    assert "requirements" not in document.metadata
    assert document.metadata["status"] == "active"
    assert document.metadata["repos"] == standalone["repositories"]
    assert document.body == "# Standalone architecture\n\n## Design\n\nTBD.\n"
    assert not plan.joinpath("requirements").exists()
    verified = store.verify_design(standalone["design_filename"])
    assert verified["requirements_path"] is None
    assert verified["requirement_count"] is None
    assert verified["status"] == "active"
    assert verified["repositories"][str(repo)]["current_changes_from_design"] == {
        "files_changed": 0,
        "insertions": 0,
        "deletions": 0,
        "binary_files": 0,
        "files": [],
    }

    paired = store.build_design(repos=[str(repo)], title="Paired architecture")
    assert paired["requirements_created"] is True
    requirements_path = Path(paired["requirements_path"])
    assert requirements_path.name == paired["design_filename"].replace(
        "design-", "requirements-", 1
    )
    assert requirements_path.is_file()


def test_verify_reports_renames_binary_files_additions_and_deletions(tmp_path: Path) -> None:
    repo = tmp_path / "service"
    git_init(repo)
    (repo / "renamed.txt").write_text("same\n", encoding="utf-8")
    (repo / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    (repo / "binary.bin").write_bytes(b"\x00\x01")
    store = DesignStore(Settings(tmp_path / "plan"))
    built = store.build_design(repos=[str(repo)], title="File changes")
    store.index_design(built["design_filename"])

    (repo / "renamed.txt").rename(repo / "new-name.txt")
    (repo / "deleted.txt").unlink()
    (repo / "binary.bin").write_bytes(b"\x00\x02")
    (repo / "added.txt").write_text("added\n", encoding="utf-8")

    changes = store.verify_design(built["design_filename"])["repositories"][str(repo)][
        "current_changes_from_design"
    ]
    assert changes == {
        "files_changed": 4,
        "insertions": 1,
        "deletions": 1,
        "binary_files": 1,
        "files": [
            {
                "path": "added.txt",
                "status": "added",
                "insertions": 1,
                "deletions": 0,
                "binary": False,
            },
            {
                "path": "binary.bin",
                "status": "modified",
                "insertions": None,
                "deletions": None,
                "binary": True,
            },
            {
                "path": "deleted.txt",
                "status": "deleted",
                "insertions": 0,
                "deletions": 1,
                "binary": False,
            },
            {
                "path": "new-name.txt",
                "status": "renamed",
                "previous_path": "renamed.txt",
                "similarity_percent": 100,
                "insertions": 0,
                "deletions": 0,
                "binary": False,
            },
        ],
    }


@pytest.mark.parametrize("status", ["cancelled", "superseded"])
def test_inactive_pair_does_not_attribute_current_changes(tmp_path: Path, status: str) -> None:
    repo = tmp_path / "service"
    git_init(repo)
    (repo / "service.py").write_text("VERSION = 1\n", encoding="utf-8")
    store = DesignStore(Settings(tmp_path / "plan"))
    built = store.build_design(repos=[str(repo)], title="Inactive change")
    store.index_design(built["design_filename"])
    design_path = store.settings.design_dir / built["design_filename"]
    requirements_path = Path(built["requirements_path"])
    for path in (design_path, requirements_path):
        path.write_text(
            path.read_text(encoding="utf-8").replace("status: active", f"status: {status}"),
            encoding="utf-8",
        )
    (repo / "service.py").write_text("VERSION = 2\n", encoding="utf-8")

    repository = store.verify_design(design_path.name)["repositories"][str(repo)]
    assert repository["current_changes_from_design"] is None
    assert repository["implementation_changes_from_design"] is None
    assert repository["current_matches_implementation"] is None


def test_relation_state_is_enforced(tmp_path: Path) -> None:
    repo = tmp_path / "service"
    git_init(repo)
    store = DesignStore(Settings(tmp_path / "plan"))
    first = store.build_design(repos=[str(repo)], title="First")
    first_doc = first["design_filename"]
    store.index_design(first_doc)

    with pytest.raises(ValueError, match="implemented"):
        store.build_design(extend=first_doc, title="Too early")

    replacement = store.build_design(supersede=first_doc, title="Replacement")
    assert replacement["relation"]["kind"] == "supersedes"
