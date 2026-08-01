from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from mcp_server_design.documents import DesignStore, Settings
from mcp_server_design.memory import MemoryFormatError, MemoryStore


def make_memory(tmp_path: Path) -> tuple[MemoryStore, Path]:
    plan = tmp_path / "plan"
    design_dir = plan / "design"
    design_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(plan)], check=True)
    design = design_dir / "design-20260731-1.md"
    design.write_text("---\ntitle: Test\n---\n# Test\n", encoding="utf-8")
    return MemoryStore(DesignStore(Settings(plan))), design


def test_issue_lifecycle_uses_stable_id_and_filters_resolved(tmp_path: Path) -> None:
    memory, design = make_memory(tmp_path)
    created = memory.set_issue(str(design), issue="A retry can duplicate output")
    issue_id = created["issue"]["id"]

    updated = memory.set_issue(
        str(design),
        issue_id=issue_id,
        status="resolved",
        resolution="Persist the operation identity before retrying.",
    )

    assert updated["issue"]["id"] == issue_id
    assert memory.list_open_issues(str(design))["open_issues"] == []


def test_refuses_to_overwrite_unowned_memory(tmp_path: Path) -> None:
    memory, design = make_memory(tmp_path)
    mapped = memory.designs.settings.memory_dir / design.name
    mapped.parent.mkdir()
    mapped.write_text("personal notes\n", encoding="utf-8")

    with pytest.raises(MemoryFormatError, match="refusing to overwrite"):
        memory.list_open_issues(str(design))
