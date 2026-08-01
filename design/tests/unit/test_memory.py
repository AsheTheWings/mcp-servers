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


def test_mutation_responses_are_compact_and_issue_ids_are_stable(tmp_path: Path) -> None:
    memory, design = make_memory(tmp_path)
    recorded = memory.record_decision(
        str(design),
        decision="Use durable operation identities",
        reasoning="Retries must not duplicate output",
    )
    created = memory.set_issue(str(design), issue="A retry can duplicate output")
    issue_id = created["issue_id"]

    updated = memory.set_issue(
        str(design),
        issue_id=issue_id,
        status="resolved",
        resolution="Persist the operation identity before retrying.",
    )

    assert set(recorded) == {"memory_path", "pruned", "memory"}
    assert set(created) == {"issue_id", "memory_path", "pruned", "memory"}
    assert set(updated) == {"issue_id", "memory_path", "pruned", "memory"}
    assert updated["issue_id"] == issue_id
    assert updated["memory"]["decisions"] == 1
    assert updated["memory"]["issues"] == 1
    assert updated["memory"]["open_issues"] == 0
    assert memory.list_open_issues(str(design))["open_issues"] == []


def test_refuses_to_overwrite_unowned_memory(tmp_path: Path) -> None:
    memory, design = make_memory(tmp_path)
    mapped = memory.designs.settings.memory_dir / design.name
    mapped.parent.mkdir()
    mapped.write_text("personal notes\n", encoding="utf-8")

    with pytest.raises(MemoryFormatError, match="refusing to overwrite"):
        memory.list_open_issues(str(design))
