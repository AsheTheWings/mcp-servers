from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from mcp import Client
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
        }
        assert tools["verify"].annotations.read_only_hint is True
        built = await client.call_tool("build", {"repos": [str(repo)], "title": "Protocol test"})
        indexed = await client.call_tool(
            "index", {"design_file_path": built.structured_content["design_path"]}
        )
        verified = await client.call_tool(
            "verify", {"design_file_path": built.structured_content["design_path"]}
        )

    assert indexed.structured_content["requirement_count"] == 1
    assert verified.is_error is False
    assert verified.structured_content["verified"] is True
