"""MCP adapter for the Browserctl development wrapper."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from .runtime import BrowserctlDevRuntime, Settings


def create_server(runtime: BrowserctlDevRuntime | None = None) -> MCPServer:
    """Build an isolated stable wrapper server."""
    active_runtime = runtime or BrowserctlDevRuntime(Settings.from_environment())

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[BrowserctlDevRuntime]:
        async with anyio.create_task_group() as task_group:
            active_runtime.attach(task_group)
            try:
                yield active_runtime
            finally:
                await active_runtime.close()

    server = MCPServer(
        "browserctl-dev",
        version="1.0.0",
        instructions=(
            "Development-only stable facade for listing and calling the active Browserctl "
            "gateway and replacing that child after a deployment."
        ),
        lifespan=lifespan,
    )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, idempotent_hint=True, open_world_hint=False
        )
    )
    async def list_tools() -> dict[str, Any]:
        """List the active Browserctl child's exact catalog and generation."""
        return await active_runtime.list_tools()

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, open_world_hint=True
        )
    )
    async def call_tool(
        ctx: Context,
        name: Annotated[str, Field(description="Underlying Browserctl tool name.")],
        arguments: Annotated[
            dict[str, Any] | None,
            Field(description="Arguments validated by the active Browserctl child."),
        ] = None,
        generation: Annotated[
            str | None,
            Field(
                description=(
                    "Optional generation returned by list_tools. The call fails if the child "
                    "was replaced after the catalog was inspected."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        """Call one tool on the active Browserctl child and forward its complete MCP result."""

        async def relay_progress(progress: float, total: float | None, message: str | None) -> None:
            await ctx.report_progress(progress, total, message)

        return await active_runtime.call_tool(
            name,
            arguments,
            generation,
            relay_progress,
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    async def restart_server() -> dict[str, Any]:
        """Replace the Browserctl child with the active development release."""
        return await active_runtime.restart()

    return server


def main() -> None:
    """Run the development wrapper over stdio."""
    create_server().run()


if __name__ == "__main__":
    main()
