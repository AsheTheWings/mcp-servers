from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from mcp import Client
from mcp.types import TextContent
from mcp_server_design.documents import DesignStore, Settings
from mcp_server_design.memory import MemoryStore
from mcp_server_design.server import create_server


@pytest.mark.anyio
async def test_protocol_discovers_and_executes_design_lifecycle(tmp_path: Path) -> None:
    plan = tmp_path / "plan"
    subprocess.run(["git", "init", "-q", str(plan)], check=True)
    repo = tmp_path / "service"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "source.txt").write_text("baseline\n", encoding="utf-8")
    designs = DesignStore(Settings(plan))
    server = create_server(designs, MemoryStore(designs))

    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {
            "build",
            "index",
            "verify",
            "capture_implementation",
            "memory_record_decision",
            "memory_set_issue",
            "memory_list_open_issues",
            "write_review",
        }
        assert tools["verify"].annotations.read_only_hint is True
        built = await client.call_tool("build", {"repos": [str(repo)], "title": "Protocol test"})
        assert "design_path" not in built.structured_content
        design_filename = built.structured_content["design_filename"]
        indexed = await client.call_tool("index", {"design_filename": design_filename})
        verified = await client.call_tool("verify", {"design_filename": design_filename})
        assert "design_path" not in indexed.structured_content
        assert "design_path" not in verified.structured_content
        recorded = await client.call_tool(
            "memory_record_decision",
            {
                "design_filename": design_filename,
                "decision": "Use durable operation identities",
                "reasoning": "Retries must not duplicate output",
            },
        )
        issue = await client.call_tool(
            "memory_set_issue",
            {
                "design_filename": design_filename,
                "issue": "A retry can duplicate output",
            },
        )
        open_issues = await client.call_tool(
            "memory_list_open_issues", {"design_filename": design_filename}
        )
        reviewed = await client.call_tool(
            "write_review",
            {
                "design_filename": design_filename,
                "content": "# Review\n\nApproved.",
                "reviewer": "conformance",
            },
        )
        captured = await client.call_tool(
            "capture_implementation", {"design_filename": design_filename}
        )
        inactive_decision = await client.call_tool(
            "memory_record_decision",
            {
                "design_filename": design_filename,
                "decision": "Late decision",
                "reasoning": "Arrived after implementation",
            },
        )
        inactive_issues = await client.call_tool(
            "memory_list_open_issues", {"design_filename": design_filename}
        )

        assert tools["index"].input_schema["properties"]["design_filename"]["description"]
        assert tools["verify"].input_schema["properties"]["design_filename"]["description"]

    assert indexed.structured_content["requirement_count"] == 1
    assert verified.is_error is False
    assert set(verified.structured_content) == {
        "design_filename",
        "requirements_path",
        "status",
        "delegated_review",
        "requirement_count",
        "repositories",
    }
    repository = verified.structured_content["repositories"][str(repo)]
    assert repository["current_changes_from_design"]["files_changed"] == 0
    assert repository["implementation_changes_from_design"] is None
    assert repository["current_matches_implementation"] is None
    assert set(recorded.structured_content) == {"memory_path", "pruned", "memory"}
    assert set(issue.structured_content) == {"issue_id", "memory_path", "pruned", "memory"}
    assert "design_path" not in open_issues.structured_content
    assert "memory_path" in open_issues.structured_content
    assert reviewed.is_error is False
    assert set(reviewed.structured_content) == {
        "design_filename",
        "review_number",
        "review_path",
    }
    review_path = Path(reviewed.structured_content["review_path"])
    assert review_path.is_absolute()
    assert review_path.parent == plan / "reviews"
    assert review_path.name == f"{design_filename[:-3]}-1.md"
    assert review_path.read_text(encoding="utf-8").endswith("# Review\n\nApproved.\n")
    assert captured.structured_content["status"] == "implemented"
    memory_errors = "".join(
        content.text
        for result in (inactive_decision, inactive_issues)
        if result.is_error
        for content in result.content
        if isinstance(content, TextContent)
    )
    assert inactive_decision.is_error is True
    assert inactive_issues.is_error is True
    assert "status is 'active'" in memory_errors
