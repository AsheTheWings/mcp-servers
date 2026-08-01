"""Design-scoped implementation decision and issue memory."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from .documents import DesignStore

MAX_MEMORY_CHARS = 80_000
ISSUE_STATUSES = ("open", "blocked", "resolved")
ENTRY_RE = re.compile(
    r"\A## (Decision|Issue) `([di]_[0-9a-f]{12})`\n```json\n(.*?)\n```\Z", re.DOTALL
)


class MemoryFormatError(ValueError):
    """Raised when a mapped memory file is malformed or belongs elsewhere."""


class MemoryStore:
    """Owns bounded, design-scoped implementation journals."""

    def __init__(self, designs: DesignStore, max_characters: int = MAX_MEMORY_CHARS) -> None:
        self.designs = designs
        self.max_characters = max_characters

    @staticmethod
    def _header(design_path: Path) -> str:
        return f"""# Design Implementation Memory

<!-- design_memory/v1 -->

Design: `../design/{design_path.name}`

This file records consequential decisions and issues that emerge while implementing the
mapped design. Keep entries concise. Do not duplicate the design, requirements, source code,
Git history, raw tool output, or conversation transcripts.
""".rstrip()

    @staticmethod
    def _git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=check,
            capture_output=True,
            text=True,
        )

    def _require_plan_repo(self) -> None:
        plan = self.designs.settings.plan_dir
        try:
            inside = self._git(plan, "rev-parse", "--is-inside-work-tree").stdout.strip()
            top = Path(self._git(plan, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError(f"PLAN_DIR is not a Git working tree: {plan}") from error
        if inside != "true" or top != plan:
            raise ValueError(f"PLAN_DIR is not the root of a Git working tree: {plan}")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".design-memory-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary)

    @contextmanager
    def _plan_lock(self) -> Iterator[None]:
        self._require_plan_repo()
        plan = self.designs.settings.plan_dir
        lock_value = self._git(plan, "rev-parse", "--git-path", "design_memory.lock").stdout.strip()
        lock_path = Path(lock_value)
        if not lock_path.is_absolute():
            lock_path = plan / lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _path(self, design_path: Path) -> Path:
        directory = self.designs.settings.memory_dir
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError(f"Design memory directory must be a regular directory: {directory}")
        path = directory / design_path.name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Design memory path must be a regular file: {path}")
        return path

    def _ensure_untracked(self, path: Path) -> None:
        self._require_plan_repo()
        relative = path.relative_to(self.designs.settings.plan_dir)
        result = self._git(
            self.designs.settings.plan_dir,
            "ls-files",
            "--error-unmatch",
            "--",
            str(relative),
            check=False,
        )
        if result.returncode == 0:
            raise ValueError(f"design memory is tracked; refusing to use it: {path}")

    @staticmethod
    def _entry(kind: str, entry_id: str, fields: dict[str, str]) -> dict[str, Any]:
        return {"kind": kind, "id": entry_id, **fields}

    @staticmethod
    def _render_entry(entry: dict[str, Any]) -> str:
        fields = {key: value for key, value in entry.items() if key not in {"kind", "id"}}
        return (
            f"## {entry['kind']} `{entry['id']}`\n```json\n"
            f"{json.dumps(fields, ensure_ascii=False, indent=2)}\n```"
        )

    def _render(self, design_path: Path, entries: list[dict[str, Any]]) -> str:
        body = "\n\n".join(self._render_entry(entry) for entry in entries)
        return self._header(design_path) + (f"\n\n{body}\n" if body else "\n")

    def _validate_issue(self, status: str, resolution: str) -> None:
        if status not in ISSUE_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(ISSUE_STATUSES)}")
        if status == "resolved" and not resolution:
            raise ValueError("resolution is required when status is resolved")

    def _parse(self, design_path: Path, content: str) -> list[dict[str, Any]]:
        header = self._header(design_path)
        normalized = content.rstrip()
        if normalized == header:
            return []
        if not normalized.startswith(header + "\n\n"):
            raise MemoryFormatError(
                f"existing memory is not mapped to {design_path.name}; refusing to overwrite it"
            )
        blocks = normalized[len(header) + 2 :].split("\n\n## ")
        entries: list[dict[str, Any]] = []
        for index, raw in enumerate(blocks):
            block = raw if index == 0 else "## " + raw
            match = ENTRY_RE.fullmatch(block)
            if not match:
                raise MemoryFormatError(f"malformed entry {index + 1} in {design_path.name} memory")
            kind, entry_id, raw_fields = match.groups()
            try:
                fields = json.loads(raw_fields)
            except json.JSONDecodeError as error:
                raise MemoryFormatError(f"invalid JSON in entry {entry_id}") from error
            expected = (
                {"decision", "reasoning"}
                if kind == "Decision"
                else {"issue", "status", "resolution"}
            )
            if (
                not isinstance(fields, dict)
                or set(fields) != expected
                or not all(isinstance(value, str) for value in fields.values())
            ):
                raise MemoryFormatError(f"entry {entry_id} has invalid {kind.lower()} fields")
            if kind == "Issue":
                self._validate_issue(fields["status"], fields["resolution"])
            if any(entry["id"] == entry_id for entry in entries):
                raise MemoryFormatError(f"duplicate memory entry ID: {entry_id}")
            entries.append(self._entry(kind, entry_id, fields))
        return entries

    @staticmethod
    def _new_id(prefix: str, entries: list[dict[str, Any]]) -> str:
        existing = {entry["id"] for entry in entries}
        while (candidate := f"{prefix}_{uuid4().hex[:12]}") in existing:
            pass
        return candidate

    @staticmethod
    def _text(name: str, value: Any, *, optional: bool = False) -> str:
        if not isinstance(value, str) or (not optional and not value.strip()):
            qualifier = "a string" if optional else "a non-empty string"
            raise ValueError(f"{name} must be {qualifier}")
        return value.strip()

    def _stats(self, design: Path, entries: list[dict[str, Any]], exists: bool) -> dict[str, Any]:
        characters = len(self._render(design, entries)) if exists else 0
        issues = [entry for entry in entries if entry["kind"] == "Issue"]
        return {
            "exists": exists,
            "characters": characters,
            "max_characters": self.max_characters,
            "remaining_characters": self.max_characters - characters,
            "entries": len(entries),
            "decisions": sum(entry["kind"] == "Decision" for entry in entries),
            "issues": len(issues),
            "open_issues": sum(entry["status"] != "resolved" for entry in issues),
        }

    def _mutate(
        self,
        design_file_path: str,
        operation: Callable[[list[dict[str, Any]]], dict[str, Any]],
    ) -> dict[str, Any]:
        design = self.designs.validate_design_path(design_file_path)
        path = self._path(design)
        with self._plan_lock():
            self._ensure_untracked(path)
            entries = self._parse(design, path.read_text(encoding="utf-8")) if path.exists() else []
            result = operation(entries)
            if any(len(self._render(design, [entry])) > self.max_characters for entry in entries):
                raise ValueError(
                    "a design memory entry is too large to fit within the memory limit"
                )
            pruned: list[dict[str, str]] = []
            while entries and len(self._render(design, entries)) > self.max_characters:
                removed = entries.pop(0)
                pruned.append({"id": removed["id"], "kind": removed["kind"].casefold()})
            self._atomic_write(path, self._render(design, entries))
            return {
                **result,
                "design_path": str(design),
                "memory_path": str(path),
                "pruned": pruned,
                "memory": {**self._stats(design, entries, True), "pruned_entries": len(pruned)},
            }

    def record_decision(
        self, design_file_path: str, decision: str, reasoning: str
    ) -> dict[str, Any]:
        normalized = self._text("decision", decision)
        rationale = self._text("reasoning", reasoning)

        def operation(entries: list[dict[str, Any]]) -> dict[str, Any]:
            entry = self._entry(
                "Decision",
                self._new_id("d", entries),
                {"decision": normalized, "reasoning": rationale},
            )
            entries.append(entry)
            return {"decision": {key: value for key, value in entry.items() if key != "kind"}}

        return self._mutate(design_file_path, operation)

    def set_issue(
        self,
        design_file_path: str,
        issue: str | None = None,
        issue_id: str | None = None,
        status: str | None = None,
        resolution: str | None = None,
    ) -> dict[str, Any]:
        if issue_id is None:
            fields = {
                "issue": self._text("issue", issue),
                "status": (status or "open").casefold(),
                "resolution": self._text("resolution", resolution or "", optional=True),
            }
            self._validate_issue(fields["status"], fields["resolution"])

            def create(entries: list[dict[str, Any]]) -> dict[str, Any]:
                entry = self._entry("Issue", self._new_id("i", entries), fields)
                entries.append(entry)
                return {"issue": {key: value for key, value in entry.items() if key != "kind"}}

            return self._mutate(design_file_path, create)

        normalized_id = self._text("issue_id", issue_id)
        if issue is None and status is None and resolution is None:
            raise ValueError("provide at least one field when updating an issue")

        def update(entries: list[dict[str, Any]]) -> dict[str, Any]:
            for index, entry in enumerate(entries):
                if entry["kind"] != "Issue" or entry["id"] != normalized_id:
                    continue
                changed = dict(entry)
                if issue is not None:
                    changed["issue"] = self._text("issue", issue)
                if status is not None:
                    changed["status"] = status.casefold()
                if resolution is not None:
                    changed["resolution"] = self._text("resolution", resolution, optional=True)
                self._validate_issue(changed["status"], changed["resolution"])
                entries.pop(index)
                entries.append(changed)
                return {"issue": {key: value for key, value in changed.items() if key != "kind"}}
            raise ValueError(f"issue not found: {normalized_id}")

        return self._mutate(design_file_path, update)

    def list_open_issues(self, design_file_path: str) -> dict[str, Any]:
        design = self.designs.validate_design_path(design_file_path)
        path = self._path(design)
        self._ensure_untracked(path)
        exists = path.exists()
        entries = self._parse(design, path.read_text(encoding="utf-8")) if exists else []
        stats = self._stats(design, entries, exists)
        characters = stats.pop("characters")
        maximum = stats.pop("max_characters")
        stats.pop("remaining_characters")
        return {
            "open_issues": [
                {key: value for key, value in entry.items() if key != "kind"}
                for entry in entries
                if entry["kind"] == "Issue" and entry["status"] != "resolved"
            ],
            "design_path": str(design),
            "memory_path": str(path),
            "memory": {
                "memory_size": f"{characters}/{maximum} ({characters * 100 / maximum:.1f}%)",
                **stats,
            },
        }
