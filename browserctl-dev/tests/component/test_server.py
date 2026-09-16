from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp import Client
from mcp.shared.dispatcher import ProgressFnT
from mcp.types import CallToolResult, ImageContent, TextContent, Tool
from mcp_server_browserctl_dev.runtime import BrowserctlDevRuntime, Settings
from mcp_server_browserctl_dev.server import create_server


@dataclass
class FakeChild:
    catalog: tuple[Tool, ...]
    responses: dict[str, CallToolResult]
    server_info: dict[str, Any] | None = field(
        default_factory=lambda: {"name": "browserctl", "version": "dev"}
    )
    closed: bool = False

    async def call_tool(
        self,
        name: str,
        _arguments: dict[str, Any] | None,
        _progress_callback: ProgressFnT | None = None,
    ) -> CallToolResult:
        return self.responses[name]

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeFactory:
    children: list[FakeChild]

    async def start(self, _release_identity: str) -> FakeChild:
        return self.children.pop(0)


def tool(name: str) -> Tool:
    return Tool(name=name, description=f"{name} description", input_schema={"type": "object"})


@pytest.mark.anyio
async def test_stable_protocol_lists_catalog_and_forwards_complete_results() -> None:
    child_result = CallToolResult(
        content=[
            TextContent(type="text", text="opened"),
            ImageContent(
                type="image",
                data=base64.b64encode(b"image-bytes").decode(),
                mime_type="image/png",
            ),
        ],
        structured_content={"pageId": 7},
        is_error=False,
        meta={"child": "metadata"},
    )
    child = FakeChild((tool("navigate_page"),), responses={"navigate_page": child_result})
    runtime = BrowserctlDevRuntime(Settings(), FakeFactory([child]), lambda: "/releases/release-1")

    async with Client(create_server(runtime)) as client:
        tools = {item.name: item for item in (await client.list_tools()).tools}
        assert set(tools) == {"list_tools", "call_tool", "restart_server"}
        assert "ctx" not in tools["call_tool"].input_schema["properties"]

        listed = await client.call_tool("list_tools", {})
        generation = listed.structured_content["generation"]
        assert listed.structured_content["release_identity"] == "/releases/release-1"
        assert listed.structured_content["tools"][0]["name"] == "navigate_page"

        forwarded = await client.call_tool(
            "call_tool",
            {
                "name": "navigate_page",
                "arguments": {"pageId": 7, "url": "https://example.test"},
                "generation": generation,
            },
        )

    assert forwarded.content == child_result.content
    assert forwarded.structured_content == child_result.structured_content
    assert forwarded.is_error == child_result.is_error
    assert forwarded.meta is not None
    assert forwarded.meta["child"] == "metadata"
    assert child.closed is True
