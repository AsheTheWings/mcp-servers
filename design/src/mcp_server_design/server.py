"""MCP adapter for design lifecycle and implementation memory tools."""

from __future__ import annotations

from threading import RLock
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .documents import DesignStore, Settings
from .memory import MemoryStore


def create_server(
    designs: DesignStore | None = None,
    memories: MemoryStore | None = None,
) -> MCPServer:
    """Build an isolated server instance for production or component tests."""
    active_designs = designs or DesignStore(Settings.from_environment())
    active_memories = memories or MemoryStore(active_designs)
    lock = RLock()
    server = MCPServer(
        "design",
        version="1.0.0",
        instructions="Manage linked design/requirements pairs and their implementation journal.",
    )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        )
    )
    def build(
        repos: list[str] | None = None,
        title: str | None = None,
        domains: list[str] | None = None,
        features: list[str] | None = None,
        description: str | None = None,
        delegated_review: bool = False,
        supersede: str | None = None,
        extend: str | None = None,
    ) -> dict[str, Any]:
        """Initialize a linked design/requirements pair and snapshot target repositories."""
        with lock:
            return active_designs.build_design(
                repos, title, domains, features, description, delegated_review, supersede, extend
            )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def index(design_file_path: str) -> dict[str, Any]:
        """Assign current sequential R* indices to substantive H3 requirements."""
        with lock:
            return active_designs.index_design(design_file_path)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, idempotent_hint=True, open_world_hint=False
        )
    )
    def verify(design_file_path: str) -> dict[str, Any]:
        """Verify pair structure and report lifecycle-specific repository changes."""
        with lock:
            return active_designs.verify_design(design_file_path)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def capture_implementation(design_file_path: str) -> dict[str, Any]:
        """Capture current repository trees and mark the linked pair implemented."""
        with lock:
            return active_designs.capture_implementation(design_file_path)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        )
    )
    def memory_record_decision(
        design_file_path: str, decision: str, reasoning: str
    ) -> dict[str, Any]:
        """Record one consequential implementation decision and its rationale."""
        with lock:
            return active_memories.record_decision(design_file_path, decision, reasoning)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        )
    )
    def memory_set_issue(
        design_file_path: str,
        issue: str | None = None,
        issue_id: str | None = None,
        status: Literal["open", "blocked", "resolved"] | None = None,
        resolution: str | None = None,
    ) -> dict[str, Any]:
        """Create or update an implementation issue in the design-scoped journal."""
        with lock:
            return active_memories.set_issue(design_file_path, issue, issue_id, status, resolution)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, idempotent_hint=True, open_world_hint=False
        )
    )
    def memory_list_open_issues(design_file_path: str) -> dict[str, Any]:
        """List unresolved issues and return bounded-memory statistics."""
        with lock:
            return active_memories.list_open_issues(design_file_path)

    return server


mcp = create_server()


def main() -> None:
    """Run the local server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
