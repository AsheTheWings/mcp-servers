"""Replaceable child lifecycle and Browserctl session restoration."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import anyio
from anyio.abc import ObjectSendStream, TaskGroup, TaskStatus
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.dispatcher import ProgressFnT
from mcp.types import CallToolResult, TextContent, Tool


class WrapperError(RuntimeError):
    """A stable wrapper operation could not complete safely."""


def _positive_float(value: str, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise WrapperError(f"{name} must be a positive number") from error
    if parsed <= 0:
        raise WrapperError(f"{name} must be a positive number")
    return parsed


@dataclass(frozen=True)
class Settings:
    """Process-start configuration for the development wrapper."""

    command: str = "/usr/local/bin/browserctl-dev-mcp"
    args: tuple[str, ...] = ()
    release_selector: Path = Path("/opt/browserctl-dev/current")
    startup_timeout_seconds: float = 30.0
    call_timeout_seconds: float = 120.0
    restore_timeout_seconds: float = 120.0

    @classmethod
    def from_environment(cls) -> Settings:
        args_value = os.environ.get("BROWSERCTL_DEV_MCP_ARGS_JSON", "[]")
        try:
            parsed_args = json.loads(args_value)
        except json.JSONDecodeError as error:
            raise WrapperError("BROWSERCTL_DEV_MCP_ARGS_JSON must be valid JSON") from error
        if not isinstance(parsed_args, list) or not all(
            isinstance(argument, str) for argument in parsed_args
        ):
            raise WrapperError("BROWSERCTL_DEV_MCP_ARGS_JSON must be a JSON array of strings")
        return cls(
            command=os.environ.get("BROWSERCTL_DEV_MCP_COMMAND", cls.command),
            args=tuple(parsed_args),
            release_selector=Path(
                os.environ.get("BROWSERCTL_DEV_RELEASE_SELECTOR", str(cls.release_selector))
            ),
            startup_timeout_seconds=_positive_float(
                os.environ.get("BROWSERCTL_DEV_MCP_STARTUP_TIMEOUT_SECONDS", "30"),
                "BROWSERCTL_DEV_MCP_STARTUP_TIMEOUT_SECONDS",
            ),
            call_timeout_seconds=_positive_float(
                os.environ.get("BROWSERCTL_DEV_MCP_CALL_TIMEOUT_SECONDS", "120"),
                "BROWSERCTL_DEV_MCP_CALL_TIMEOUT_SECONDS",
            ),
            restore_timeout_seconds=_positive_float(
                os.environ.get("BROWSERCTL_DEV_MCP_RESTORE_TIMEOUT_SECONDS", "120"),
                "BROWSERCTL_DEV_MCP_RESTORE_TIMEOUT_SECONDS",
            ),
        )


class ChildHandle(Protocol):
    """One initialized Browserctl child generation."""

    catalog: tuple[Tool, ...]
    server_info: dict[str, Any] | None

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        progress_callback: ProgressFnT | None = None,
    ) -> CallToolResult: ...

    async def close(self) -> None: ...


class ChildFactory(Protocol):
    async def start(self, release_identity: str) -> ChildHandle: ...


@dataclass
class _CallCommand:
    name: str
    arguments: dict[str, Any] | None
    progress_callback: ProgressFnT | None
    completed: anyio.Event = field(default_factory=anyio.Event)
    result: CallToolResult | None = None
    error: BaseException | None = None


@dataclass
class _CloseCommand:
    completed: anyio.Event = field(default_factory=anyio.Event)


_Command = _CallCommand | _CloseCommand


class _StdioChildHandle:
    def __init__(
        self,
        catalog: tuple[Tool, ...],
        server_info: dict[str, Any] | None,
        sender: ObjectSendStream[_Command],
    ) -> None:
        self.catalog = catalog
        self.server_info = server_info
        self._sender = sender
        self._closed = False
        self._terminated = anyio.Event()
        self._termination_error: BaseException | None = None

    def mark_terminated(self, error: BaseException | None) -> None:
        self._termination_error = error
        self._terminated.set()

    def _raise_terminated(self) -> None:
        if self._termination_error is not None:
            raise WrapperError("Browserctl child process terminated") from self._termination_error
        raise WrapperError("Browserctl child process terminated")

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        progress_callback: ProgressFnT | None = None,
    ) -> CallToolResult:
        if self._closed:
            raise WrapperError("Browserctl child generation is closed")
        if self._terminated.is_set():
            self._raise_terminated()
        command = _CallCommand(name, arguments, progress_callback)
        try:
            await self._sender.send(command)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError) as error:
            raise WrapperError("Browserctl child process terminated") from error
        await command.completed.wait()
        if command.error is not None:
            raise command.error
        if command.result is None:  # pragma: no cover - guarded by the worker
            raise WrapperError("Browserctl child returned no tool result")
        return command.result

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._terminated.is_set():
            await self._sender.aclose()
            return
        command = _CloseCommand()
        try:
            await self._sender.send(command)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            await self._sender.aclose()
            return
        await command.completed.wait()
        await self._sender.aclose()


class StdioChildFactory:
    """Start child clients in supervisor-owned tasks."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.task_group: TaskGroup | None = None

    def attach(self, task_group: TaskGroup) -> None:
        self.task_group = task_group

    async def start(self, release_identity: str) -> ChildHandle:
        if self.task_group is None:
            raise WrapperError("Browserctl child supervisor is not running")
        with anyio.fail_after(self.settings.startup_timeout_seconds):
            return await self.task_group.start(self._run, release_identity)

    async def _run(
        self,
        release_identity: str,
        *,
        task_status: TaskStatus[ChildHandle],
    ) -> None:
        del release_identity
        sender, receiver = anyio.create_memory_object_stream[_Command](64)
        parameters = StdioServerParameters(
            command=self.settings.command,
            args=list(self.settings.args),
        )
        transport = stdio_client(parameters)
        handle: _StdioChildHandle | None = None
        close_command: _CloseCommand | None = None
        termination_error: BaseException | None = None
        try:
            async with Client(
                transport,
                read_timeout_seconds=self.settings.call_timeout_seconds,
                mode="auto",
                cache=None,
            ) as client:
                listed = await client.list_tools()
                info = client.session.server_info
                server_info = info.model_dump(mode="json", by_alias=True) if info else None
                handle = _StdioChildHandle(tuple(listed.tools), server_info, sender)
                task_status.started(handle)

                async def execute(command: _CallCommand) -> None:
                    try:
                        command.result = await client.call_tool(
                            command.name,
                            command.arguments,
                            progress_callback=command.progress_callback,
                        )
                    except BaseException as error:
                        command.error = error
                    finally:
                        command.completed.set()

                async with receiver, anyio.create_task_group() as calls:
                    async for command in receiver:
                        if isinstance(command, _CloseCommand):
                            close_command = command
                            break
                        calls.start_soon(execute, command)
        except BaseException as error:
            if handle is None:
                raise
            termination_error = error
        finally:
            if handle is not None:
                handle.mark_terminated(termination_error)
            if close_command is not None:
                close_command.completed.set()


@dataclass
class _Generation:
    id: str
    release_identity: str
    child: ChildHandle
    in_flight: int = 0


def _payload(result: CallToolResult) -> dict[str, Any] | None:
    if isinstance(result.structured_content, dict):
        return result.structured_content
    for content in result.content:
        if not isinstance(content, TextContent):
            continue
        try:
            parsed = json.loads(content.text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _session(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    nested = payload.get("session")
    return nested if isinstance(nested, dict) else payload


class BrowserctlDevRuntime:
    """Route stable wrapper calls across replaceable Browserctl children."""

    def __init__(
        self,
        settings: Settings,
        factory: ChildFactory | None = None,
        release_identity: Callable[[], str] | None = None,
    ) -> None:
        self.settings = settings
        self.factory = factory or StdioChildFactory(settings)
        self._release_identity = release_identity or self._resolve_release_identity
        self._active: _Generation | None = None
        self._condition = anyio.Condition()
        self._restart_lock = anyio.Lock()
        self._cutover = False
        self._controlled_sessions: list[str] = []
        self._selected_session: str | None = None

    def attach(self, task_group: TaskGroup) -> None:
        attach = getattr(self.factory, "attach", None)
        if attach is not None:
            attach(task_group)

    def _resolve_release_identity(self) -> str:
        try:
            return str(self.settings.release_selector.resolve(strict=True))
        except OSError as error:
            raise WrapperError(
                f"Browserctl release selector is unavailable: {self.settings.release_selector}"
            ) from error

    async def close(self) -> None:
        async with self._restart_lock:
            async with self._condition:
                self._cutover = True
                while self._active is not None and self._active.in_flight:
                    await self._condition.wait()
                generation = self._active
                self._active = None
            if generation is not None:
                await generation.child.close()

    async def _start_consistent_child(self) -> _Generation:
        for _attempt in range(3):
            before = self._release_identity()
            child = await self.factory.start(before)
            after = self._release_identity()
            if before == after:
                return _Generation(uuid.uuid4().hex, after, child)
            await child.close()
        raise WrapperError("Browserctl development release changed repeatedly during startup")

    async def _ensure_active(self) -> _Generation:
        if self._active is not None:
            return self._active
        async with self._restart_lock:
            if self._active is None:
                self._active = await self._start_consistent_child()
            return self._active

    async def list_tools(self) -> dict[str, Any]:
        generation = await self._ensure_active()
        async with self._condition:
            while self._cutover:
                await self._condition.wait()
            generation = self._active
            if generation is None:  # pragma: no cover - close races only at process shutdown
                raise WrapperError("Browserctl child is unavailable")
            return {
                "generation": generation.id,
                "release_identity": generation.release_identity,
                "server_info": generation.child.server_info,
                "tools": [
                    tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for tool in generation.child.catalog
                ],
            }

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        generation: str | None = None,
        progress_callback: ProgressFnT | None = None,
    ) -> CallToolResult:
        await self._ensure_active()
        async with self._condition:
            while self._cutover:
                await self._condition.wait()
            active = self._active
            if active is None:  # pragma: no cover - close races only at process shutdown
                raise WrapperError("Browserctl child is unavailable")
            if generation is not None and generation != active.id:
                raise WrapperError(
                    f"Browserctl generation changed; expected {generation}, current {active.id}"
                )
            active.in_flight += 1
        try:
            result = await active.child.call_tool(name, arguments, progress_callback)
            if not result.is_error:
                self._track_session_state(name, arguments or {}, result)
            return result
        finally:
            async with self._condition:
                active.in_flight -= 1
                self._condition.notify_all()

    def _remember(self, session_id: str) -> None:
        if session_id not in self._controlled_sessions:
            self._controlled_sessions.append(session_id)

    def _forget(self, session_id: str) -> None:
        if session_id in self._controlled_sessions:
            self._controlled_sessions.remove(session_id)
        if self._selected_session == session_id:
            self._selected_session = None

    def _track_session_state(
        self,
        name: str,
        arguments: dict[str, Any],
        result: CallToolResult,
    ) -> None:
        payload = _payload(result)
        session = _session(payload)
        if name == "session_create" and session is not None:
            session_id = session.get("id")
            if isinstance(session_id, str):
                self._remember(session_id)
                if payload is not None and payload.get("selected") is True:
                    self._selected_session = session_id
        elif name == "session_select" and payload is not None:
            session_id = payload.get("selected")
            if isinstance(session_id, str):
                self._remember(session_id)
                self._selected_session = session_id
        elif name == "session_release" and payload is not None:
            session_id = payload.get("released")
            if isinstance(session_id, str):
                self._forget(session_id)
        elif name in {"session_stop", "session_complete"}:
            session_id = session.get("id") if session is not None else arguments.get("sessionId")
            if not isinstance(session_id, str):
                session_id = self._selected_session
            if isinstance(session_id, str):
                self._forget(session_id)

    @staticmethod
    def _require_success(result: CallToolResult, operation: str) -> dict[str, Any] | None:
        if result.is_error:
            text = "".join(
                content.text for content in result.content if isinstance(content, TextContent)
            )
            raise WrapperError(f"{operation} failed during Browserctl restart: {text}")
        return _payload(result)

    async def _restore(self, candidate: _Generation) -> list[str]:
        if self._controlled_sessions and self._selected_session is None:
            raise WrapperError(
                "Cannot preserve Browserctl bindings without a selected session; "
                "select one session or release the remaining bindings before restarting"
            )
        restored: list[str] = []
        with anyio.fail_after(self.settings.restore_timeout_seconds):
            for session_id in self._controlled_sessions:
                observed = self._require_success(
                    await candidate.child.call_tool("session_get", {"sessionId": session_id}),
                    f"session_get({session_id})",
                )
                session = _session(observed)
                state = session.get("state") if session is not None else None
                if state in {"queued", "provisioning"}:
                    waited = self._require_success(
                        await candidate.child.call_tool(
                            "session_wait",
                            {
                                "sessionId": session_id,
                                "waitFor": "running",
                                "timeoutMs": min(
                                    round(self.settings.restore_timeout_seconds * 1000), 115_000
                                ),
                            },
                        ),
                        f"session_wait({session_id})",
                    )
                    session = _session(waited)
                    state = session.get("state") if session is not None else None
                if state in {"succeeded", "failed", "blocked", "cancelled"}:
                    continue
                if state != "running":
                    raise WrapperError(
                        f"Session {session_id} cannot be restored from state {state!r}"
                    )
                self._require_success(
                    await candidate.child.call_tool("session_select", {"sessionId": session_id}),
                    f"session_select({session_id})",
                )
                restored.append(session_id)
            if self._selected_session is not None:
                self._require_success(
                    await candidate.child.call_tool(
                        "session_select", {"sessionId": self._selected_session}
                    ),
                    f"session_select({self._selected_session})",
                )
        return restored

    async def restart(self) -> dict[str, Any]:
        async with self._restart_lock:
            candidate = await self._start_consistent_child()
            old: _Generation | None = None
            try:
                async with self._condition:
                    self._cutover = True
                    while self._active is not None and self._active.in_flight:
                        await self._condition.wait()
                    old = self._active
                restored = await self._restore(candidate)
                async with self._condition:
                    self._active = candidate
                    self._cutover = False
                    self._condition.notify_all()
            except BaseException:
                async with self._condition:
                    self._cutover = False
                    self._condition.notify_all()
                await candidate.child.close()
                raise
            if old is not None:
                await old.child.close()
            return {
                "generation": candidate.id,
                "release_identity": candidate.release_identity,
                "server_info": candidate.child.server_info,
                "tool_count": len(candidate.child.catalog),
                "restored_sessions": restored,
                "selected_session": self._selected_session,
            }
