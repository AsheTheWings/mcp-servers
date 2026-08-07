"""Design-scoped implementation review reports persisted as markdown."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from .documents import DesignStore

MAX_CREATE_ATTEMPTS = 100


class ReviewStore:
    """Owns design-scoped review reports under the plan reviews directory."""

    def __init__(self, designs: DesignStore) -> None:
        self.designs = designs

    @staticmethod
    def _text(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _create_exclusive(path: Path, content: str) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def _render(self, design: Path, reviewer: str | None, content: str) -> str:
        metadata: dict[str, Any] = {"design": os.path.relpath(design, self._directory())}
        if reviewer is not None:
            metadata["reviewer"] = reviewer
        header = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).rstrip()
        return f"---\n{header}\n---\n\n{content}\n"

    def _directory(self) -> Path:
        directory = self.designs.settings.reviews_dir
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError(f"Review directory must be a regular directory: {directory}")
        return directory

    def write_review(
        self, design_doc: str, content: str, reviewer: str | None = None
    ) -> dict[str, Any]:
        design = self.designs.validate_design_path(design_doc)
        body = self._text("content", content)
        role = self._text("reviewer", reviewer) if reviewer is not None else None
        directory = self._directory()
        directory.mkdir(parents=True, exist_ok=True)
        pattern = re.compile(rf"{re.escape(design.stem)}-(\d+)\.md")
        next_number = (
            max(
                (
                    int(match.group(1))
                    for path in directory.iterdir()
                    if (match := pattern.fullmatch(path.name))
                ),
                default=0,
            )
            + 1
        )
        rendered = self._render(design, role, body)
        for number in range(next_number, next_number + MAX_CREATE_ATTEMPTS):
            path = directory / f"{design.stem}-{number}.md"
            try:
                self._create_exclusive(path, rendered)
            except FileExistsError:
                continue
            return {
                "design_filename": design.name,
                "review_number": number,
                "review_path": str(path),
            }
        raise ValueError(f"Could not allocate a review number for {design.name}")
