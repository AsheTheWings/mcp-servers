"""MCP adapter for design lifecycle and implementation memory tools."""

from __future__ import annotations

from threading import RLock
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .documents import DesignStore, Settings
from .memory import MemoryStore

DesignFilename = Annotated[
    str,
    Field(
        description=(
            "Generated design filename, such as design-20260801-1.md. "
            "Use the filename instead of a full path when possible; full paths are also accepted."
        )
    ),
]


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
        instructions=(
            "Scaffold standalone designs or linked design/requirements pairs, and manage "
            "paired implementation journals."
        ),
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
        include_requirements: Annotated[
            bool,
            Field(
                description=(
                    "Create a linked requirements document when true; scaffold only the design "
                    "document when false."
                )
            ),
        ] = True,
        supersede: str | None = None,
        extend: str | None = None,
    ) -> dict[str, Any]:
        """Initialize a design, optionally with linked requirements, and snapshot repositories."""
        with lock:
            return active_designs.build_design(
                repos=repos,
                title=title,
                domains=domains,
                features=features,
                description=description,
                delegated_review=delegated_review,
                supersede=supersede,
                extend=extend,
                include_requirements=include_requirements,
            )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def index(design_filename: DesignFilename) -> dict[str, Any]:
        """Assign current sequential R* indices to substantive H3 requirements."""
        with lock:
            return active_designs.index_design(design_filename)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, idempotent_hint=True, open_world_hint=False
        )
    )
    def verify(design_filename: DesignFilename) -> dict[str, Any]:
        """Verify a standalone design or linked pair and report repository changes."""
        with lock:
            return active_designs.verify_design(design_filename)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def capture_implementation(design_filename: DesignFilename) -> dict[str, Any]:
        """Capture current repository trees and mark the linked pair implemented."""
        with lock:
            return active_designs.capture_implementation(design_filename)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        )
    )
    def memory_record_decision(
        design_filename: DesignFilename, decision: str, reasoning: str
    ) -> dict[str, Any]:
        """Record one consequential implementation decision; active designs only."""
        with lock:
            return active_memories.record_decision(design_filename, decision, reasoning)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        )
    )
    def memory_set_issue(
        design_filename: DesignFilename,
        issue: str | None = None,
        issue_id: str | None = None,
        status: Literal["open", "blocked", "resolved"] | None = None,
        resolution: str | None = None,
    ) -> dict[str, Any]:
        """Create or update an implementation issue in the journal; active designs only."""
        with lock:
            return active_memories.set_issue(design_filename, issue, issue_id, status, resolution)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True, idempotent_hint=True, open_world_hint=False
        )
    )
    def memory_list_open_issues(design_filename: DesignFilename) -> dict[str, Any]:
        """List unresolved issues and memory statistics; active designs only."""
        with lock:
            return active_memories.list_open_issues(design_filename)

    return server


def main() -> None:
    """Run the local server over stdio."""
    create_server().run()


if __name__ == "__main__":
    main()
