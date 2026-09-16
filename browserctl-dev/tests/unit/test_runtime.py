from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anyio
import pytest
from mcp.shared.dispatcher import ProgressFnT
from mcp.types import CallToolResult, TextContent, Tool
from mcp_server_browserctl_dev.runtime import BrowserctlDevRuntime, Settings, WrapperError

Response = CallToolResult | Callable[[dict[str, Any] | None], Awaitable[CallToolResult]]


def result(payload: dict[str, Any] | None = None, text: str = "ok") -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
        is_error=False,
    )


@dataclass
class FakeChild:
    catalog: tuple[Tool, ...]
    responses: dict[str, Response] = field(default_factory=dict)
    server_info: dict[str, Any] | None = field(
        default_factory=lambda: {"name": "browserctl", "version": "dev"}
    )
    calls: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)
    closed: bool = False

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        progress_callback: ProgressFnT | None = None,
    ) -> CallToolResult:
        del progress_callback
        self.calls.append((name, arguments))
        response = self.responses.get(name, result())
        if isinstance(response, CallToolResult):
            return response
        return await response(arguments)

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeFactory:
    children: list[FakeChild]

    async def start(self, _release_identity: str) -> FakeChild:
        if not self.children:
            raise RuntimeError("no fake child available")
        return self.children.pop(0)


def tool(name: str) -> Tool:
    return Tool(name=name, description=f"{name} description", input_schema={"type": "object"})


def runtime(factory: FakeFactory) -> BrowserctlDevRuntime:
    return BrowserctlDevRuntime(Settings(), factory, lambda: "/releases/current")


@pytest.mark.anyio
async def test_generation_fence_rejects_a_stale_catalog() -> None:
    child = FakeChild((tool("one"),))
    active = runtime(FakeFactory([child]))
    catalog = await active.list_tools()

    with pytest.raises(WrapperError, match="generation changed"):
        await active.call_tool("one", generation="stale")

    assert catalog["generation"] != "stale"
    assert child.calls == []
    await active.close()


@pytest.mark.anyio
async def test_restart_restores_controlled_sessions_and_selected_session() -> None:
    first = FakeChild(
        (tool("session_create"), tool("session_select")),
        responses={
            "session_create": result(
                {"session": {"id": "session-1", "state": "running"}, "selected": True}
            )
        },
    )
    replacement = FakeChild(
        (tool("new_tool"),),
        responses={
            "session_get": result({"id": "session-1", "state": "running"}),
            "session_select": result({"selected": "session-1"}),
        },
    )
    active = runtime(FakeFactory([first, replacement]))
    await active.call_tool("session_create", {})

    restarted = await active.restart()

    assert restarted["restored_sessions"] == ["session-1"]
    assert restarted["selected_session"] == "session-1"
    assert replacement.calls == [
        ("session_get", {"sessionId": "session-1"}),
        ("session_select", {"sessionId": "session-1"}),
        ("session_select", {"sessionId": "session-1"}),
    ]
    assert first.closed is True
    assert (await active.list_tools())["tools"][0]["name"] == "new_tool"
    await active.close()


@pytest.mark.anyio
async def test_failed_restoration_leaves_old_generation_active() -> None:
    first = FakeChild(
        (tool("session_create"),),
        responses={
            "session_create": result(
                {"session": {"id": "session-1", "state": "running"}, "selected": True}
            )
        },
    )
    replacement = FakeChild(
        (tool("new_tool"),),
        responses={
            "session_get": CallToolResult(
                content=[TextContent(type="text", text="not enrolled")], is_error=True
            )
        },
    )
    active = runtime(FakeFactory([first, replacement]))
    before = await active.list_tools()
    await active.call_tool("session_create", {})

    with pytest.raises(WrapperError, match="not enrolled"):
        await active.restart()

    after = await active.list_tools()
    assert after["generation"] == before["generation"]
    assert first.closed is False
    assert replacement.closed is True
    await active.close()


@pytest.mark.anyio
async def test_failed_startup_leaves_old_generation_active() -> None:
    first = FakeChild((tool("existing_tool"),))
    active = runtime(FakeFactory([first]))
    before = await active.list_tools()

    with pytest.raises(RuntimeError, match="no fake child"):
        await active.restart()

    after = await active.list_tools()
    assert after["generation"] == before["generation"]
    assert first.closed is False
    await active.close()


@pytest.mark.anyio
async def test_restart_waits_for_an_in_flight_call_before_cutover() -> None:
    entered = anyio.Event()
    release = anyio.Event()

    async def delayed(_arguments: dict[str, Any] | None) -> CallToolResult:
        entered.set()
        await release.wait()
        return result(text="finished")

    first = FakeChild((tool("slow"),), responses={"slow": delayed})
    replacement = FakeChild((tool("new_tool"),))
    active = runtime(FakeFactory([first, replacement]))
    restart_done = anyio.Event()

    async def restart() -> None:
        await active.restart()
        restart_done.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(active.call_tool, "slow")
        await entered.wait()
        tasks.start_soon(restart)
        await anyio.sleep(0)
        assert restart_done.is_set() is False
        assert first.closed is False
        release.set()

    assert restart_done.is_set() is True
    assert first.closed is True
    await active.close()


@pytest.mark.anyio
async def test_release_without_another_selection_is_not_silently_changed() -> None:
    first = FakeChild(
        (tool("session_create"), tool("session_release")),
        responses={
            "session_create": result(
                {"session": {"id": "session-1", "state": "running"}, "selected": True}
            ),
            "session_release": result({"released": "session-1"}),
        },
    )
    replacement = FakeChild((tool("new_tool"),))
    active = runtime(FakeFactory([first, replacement]))
    await active.call_tool("session_create", {})
    await active.call_tool("session_release", {"sessionId": "session-1"})

    restarted = await active.restart()

    assert restarted["restored_sessions"] == []
    assert replacement.calls == []
    await active.close()
