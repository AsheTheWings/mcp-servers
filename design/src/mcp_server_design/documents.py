"""Paired design and requirements document lifecycle operations."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    """Filesystem inputs fixed for one server process."""

    plan_dir: Path

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(Path(os.environ.get("PLAN_DIR", "/root/Desktop/plan")).expanduser().resolve())

    @property
    def design_dir(self) -> Path:
        return self.plan_dir / "design"

    @property
    def requirements_dir(self) -> Path:
        return self.plan_dir / "requirements"

    @property
    def memory_dir(self) -> Path:
        return self.plan_dir / "memory"


@dataclass
class Document:
    metadata: dict[str, Any]
    body: str

    def render(self) -> str:
        header = yaml.safe_dump(self.metadata, sort_keys=False, allow_unicode=True).rstrip()
        return f"---\n{header}\n---\n{self.body}"


class DesignStore:
    """Owns paired document validation, mutation, and repository snapshots."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def _required_text(name: str, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _string_list(name: str, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(f"{name} must be a list of non-empty strings")
        return [item.strip() for item in value]

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary)

    @staticmethod
    def _git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=check,
            capture_output=True,
            text=True,
        )

    def resolve_repo_path(self, value: str, reference_dir: Path) -> Path:
        candidates = [value]
        if "/" in value and not value.startswith("/"):
            candidates.append(value.rsplit("/", 1)[0])
        for candidate in candidates:
            path = Path(candidate)
            possible = (
                [path]
                if path.is_absolute()
                else [reference_dir / path, self.settings.plan_dir.parent / path, Path.cwd() / path]
            )
            for item in possible:
                resolved = item.resolve()
                if not resolved.is_dir():
                    continue
                result = self._git(resolved, "rev-parse", "--show-toplevel", check=False)
                if result.returncode == 0:
                    return Path(result.stdout.strip()).resolve()
        raise ValueError(f"Could not resolve Git repository: {value}")

    def generate_tree_sha(self, repo: Path) -> str:
        descriptor, index_name = tempfile.mkstemp()
        os.close(descriptor)
        os.unlink(index_name)
        environment = os.environ.copy()
        environment["GIT_INDEX_FILE"] = index_name
        try:
            for arguments in (("read-tree", "--empty"), ("add", "-A", "--", "."), ("write-tree",)):
                result = subprocess.run(
                    ["git", "-C", str(repo), *arguments],
                    capture_output=True,
                    env=environment,
                    text=True,
                )
                if result.returncode:
                    detail = result.stderr.strip() or result.stdout.strip()
                    raise ValueError(f"Failed to generate Git tree SHA for {repo}: {detail}")
            return result.stdout.strip()
        finally:
            with suppress(FileNotFoundError):
                os.unlink(index_name)

    @staticmethod
    def parse_document(content: str) -> Document:
        if not content.startswith("---\n"):
            raise ValueError("YAML frontmatter not found")
        try:
            raw_header, body = content[4:].split("\n---\n", 1)
        except ValueError as error:
            raise ValueError("YAML frontmatter not terminated") from error
        metadata = yaml.safe_load(raw_header)
        if not isinstance(metadata, dict):
            raise ValueError("YAML frontmatter must be a mapping")
        return Document(metadata, body)

    def read_document(self, path: Path, label: str) -> Document:
        try:
            return self.parse_document(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError(f"Failed to read {label} '{path}': {error}") from error
        except (ValueError, yaml.YAMLError) as error:
            raise ValueError(f"Invalid {label} '{path}': {error}") from error

    def validate_document_path(self, value: str, directory: Path, prefix: str, label: str) -> Path:
        raw = self._required_text(f"{label.lower()}_path", value)
        path = Path(raw).expanduser()
        if not path.is_absolute():
            candidate = (directory / path).resolve()
            path = candidate if candidate.exists() else path.resolve()
        else:
            path = path.resolve()
        if path.parent != directory.resolve():
            raise PermissionError(f"{label} must be inside {directory.resolve()}")
        if not re.fullmatch(rf"{prefix}-\d{{8}}-\d+\.md", path.name):
            raise ValueError(f"Expected a generated {prefix}-YYYYMMDD-N.md file")
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")
        return path

    def validate_design_path(self, value: str) -> Path:
        return self.validate_document_path(
            value, self.settings.design_dir, "design", "Design document"
        )

    def validate_requirements_path(self, value: str) -> Path:
        return self.validate_document_path(
            value, self.settings.requirements_dir, "requirements", "Requirements document"
        )

    @staticmethod
    def _reference(owner: Path, metadata: dict[str, Any], key: str) -> Path | None:
        value = metadata.get(key)
        return (owner.parent / value).resolve() if isinstance(value, str) and value else None

    def resolve_pair(self, design_file_path: str) -> tuple[Path, Path]:
        design = self.validate_design_path(design_file_path)
        document = self.read_document(design, "design document")
        requirements = self._reference(design, document.metadata, "requirements")
        if requirements is None:
            raise ValueError("Design document does not reference a requirements document")
        return design, self.validate_requirements_path(str(requirements))

    @staticmethod
    def _snapshots(metadata: dict[str, Any]) -> dict[str, dict[str, str | None]]:
        raw = metadata.get("repos")
        if not isinstance(raw, dict):
            return {}
        snapshots: dict[str, dict[str, str | None]] = {}
        for repo, value in raw.items():
            if isinstance(value, str):
                snapshots[str(repo)] = {"design": value, "implementation": None}
            elif isinstance(value, dict):
                snapshots[str(repo)] = {
                    "design": str(value.get("design", "")),
                    "implementation": str(value["implementation"])
                    if value.get("implementation")
                    else None,
                }
        return snapshots

    def _next_path(self, directory: Path, prefix: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        numbers = [
            int(match.group(1))
            for path in directory.glob(f"{prefix}-{today}-*.md")
            if (match := re.fullmatch(rf"{prefix}-{today}-(\d+)\.md", path.name))
        ]
        return directory / f"{prefix}-{today}-{max(numbers, default=0) + 1}.md"

    def _related(self, value: str | None, relation: str) -> tuple[Path, Path, Document] | None:
        if value is None:
            return None
        design, requirements = self.resolve_pair(value)
        document = self.read_document(design, f"{relation} design document")
        snapshots = self._snapshots(document.metadata)
        status = document.metadata.get("status")
        if relation == "supersedes" and (
            status != "active" or any(item["implementation"] for item in snapshots.values())
        ):
            raise ValueError("Can only supersede an active, unimplemented design")
        if relation == "extends" and status != "implemented":
            raise ValueError("Can only extend an implemented design")
        return design, requirements, document

    def build_design(
        self,
        repos: Any = None,
        title: str | None = None,
        domains: Any = None,
        features: Any = None,
        description: str | None = None,
        delegated_review: bool = False,
        supersede: str | None = None,
        extend: str | None = None,
    ) -> dict[str, Any]:
        requested_repos = self._string_list("repos", repos)
        target_domains = self._string_list("domains", domains)
        target_features = self._string_list("features", features)
        if not isinstance(delegated_review, bool):
            raise ValueError("delegated_review must be a boolean")
        if supersede and extend:
            raise ValueError("supersede and extend are mutually exclusive")
        relation = "supersedes" if supersede else "extends" if extend else None
        related = self._related(supersede or extend, relation or "") if relation else None
        if not requested_repos and related:
            requested_repos = list(self._snapshots(related[2].metadata))
        if not requested_repos:
            raise ValueError("At least one repository is required")
        repositories = list(
            dict.fromkeys(
                self.resolve_repo_path(value, self.settings.design_dir) for value in requested_repos
            )
        )
        snapshots = {
            str(repo): {"design": self.generate_tree_sha(repo), "implementation": None}
            for repo in repositories
        }
        if related:
            target_domains = target_domains or list(related[2].metadata.get("domains", []))
            target_features = target_features or list(related[2].metadata.get("features", []))

        subject = ", ".join(target_features or target_domains) or "[feature/domain]"
        design_title = (
            title.strip() if isinstance(title, str) and title.strip() else "[Short design title]"
        )
        design_description = (
            description.strip()
            if isinstance(description, str) and description.strip()
            else f"Design for {subject}."
        )
        design_path = self._next_path(self.settings.design_dir, "design")
        requirements_path = self._next_path(self.settings.requirements_dir, "requirements")
        design_metadata: dict[str, Any] = {
            "title": design_title,
            "description": design_description,
            "status": "active",
            "delegated_review": delegated_review,
            "requirements": os.path.relpath(requirements_path, design_path.parent),
            "repos": snapshots,
            "domains": target_domains,
            "features": target_features,
        }
        requirements_metadata: dict[str, Any] = {
            "title": f"{design_title} Requirements",
            "description": f"Canonical implementation requirements for {subject}.",
            "status": "active",
            "design": os.path.relpath(design_path, requirements_path.parent),
            "repos": snapshots,
            "domains": target_domains,
            "features": target_features,
        }
        if relation and related:
            design_metadata[relation] = os.path.relpath(related[0], design_path.parent)
            requirements_metadata[relation] = os.path.relpath(related[1], requirements_path.parent)
        design = Document(design_metadata, f"# {design_title}\n\n## Design\n\nTBD.\n")
        requirements_body = f"# {design_title} Requirements\n\n" + "".join(
            f"## {repo}\n\n### Requirement\n\nTBD.\n\n" for repo in repositories
        )
        self._atomic_write(design_path, design.render())
        try:
            self._atomic_write(
                requirements_path, Document(requirements_metadata, requirements_body).render()
            )
        except Exception:
            design_path.unlink(missing_ok=True)
            raise
        return {
            "design_path": str(design_path),
            "requirements_path": str(requirements_path),
            "status": "active",
            "delegated_review": delegated_review,
            "repositories": snapshots,
            "domains": target_domains,
            "features": target_features,
            "relation": {"kind": relation, "design_path": str(related[0])}
            if relation and related
            else None,
        }

    @staticmethod
    def _requirement_headings(body: str) -> list[tuple[int, int | None, str]]:
        lines = body.splitlines(keepends=True)
        headings: list[tuple[int, int | None, str]] = []
        for position, line in enumerate(lines):
            match = re.fullmatch(r"###\s+(?:R(\d+)\.\s+)?(.+?)\s*", line.strip())
            if not match:
                continue
            end = position + 1
            while end < len(lines) and not re.match(r"^#{1,3}\s+", lines[end]):
                end += 1
            if any(item.strip() for item in lines[position + 1 : end]):
                headings.append(
                    (position, int(match.group(1)) if match.group(1) else None, match.group(2))
                )
        return headings

    def _pair_state(
        self, design_path: Path, requirements_path: Path
    ) -> tuple[Document, Document, dict[str, dict[str, str | None]]]:
        design = self.read_document(design_path, "design document")
        requirements = self.read_document(requirements_path, "requirements document")
        if self._reference(design_path, design.metadata, "requirements") != requirements_path:
            raise ValueError("Design document does not link to the requirements document")
        if self._reference(requirements_path, requirements.metadata, "design") != design_path:
            raise ValueError("Requirements document does not link back to the design document")
        snapshots = self._snapshots(design.metadata)
        if not snapshots or snapshots != self._snapshots(requirements.metadata):
            raise ValueError("Document pair has missing or different repository snapshots")
        status = design.metadata.get("status")
        if status != requirements.metadata.get("status") or status not in {
            "active",
            "implemented",
            "superseded",
            "cancelled",
        }:
            raise ValueError("Document pair has different or unsupported statuses")
        if not isinstance(design.metadata.get("delegated_review"), bool):
            raise ValueError("Design document must define delegated_review as true or false")
        implementations = [item["implementation"] is not None for item in snapshots.values()]
        if any(implementations) != all(implementations) or (
            any(implementations) and status != "implemented"
        ):
            raise ValueError(
                "Implementation snapshots must cover every repository and require "
                "implemented status"
            )
        if f"# {design.metadata.get('title')}" not in design.body:
            raise ValueError("Design document H1 does not match its title")
        if f"# {requirements.metadata.get('title')}" not in requirements.body:
            raise ValueError("Requirements document H1 does not match its title")
        for repo in snapshots:
            if f"## {repo}" not in requirements.body:
                raise ValueError(f"Requirements document is missing repository section '## {repo}'")
        for relation in ("extends", "supersedes"):
            design_link = self._reference(design_path, design.metadata, relation)
            requirements_link = self._reference(requirements_path, requirements.metadata, relation)
            if bool(design_link) != bool(requirements_link):
                raise ValueError(f"Design and requirements contain different {relation} links")
            if design_link:
                related_design, related_requirements = self.resolve_pair(str(design_link))
                if related_requirements != requirements_link:
                    raise ValueError(f"Document pair contains inconsistent {relation} links")
                related = self.read_document(related_design, f"{relation} design document")
                if relation == "extends" and related.metadata.get("status") != "implemented":
                    raise ValueError("An extends link must reference an implemented design")
        return design, requirements, snapshots

    def index_design(self, design_file_path: str) -> dict[str, Any]:
        design_path, requirements_path = self.resolve_pair(design_file_path)
        document = self.read_document(requirements_path, "requirements document")
        headings = self._requirement_headings(document.body)
        if not headings:
            raise ValueError("Requirements document contains no substantive H3 requirements")
        lines = document.body.splitlines(keepends=True)
        changed = False
        for index, (position, _, title) in enumerate(headings, 1):
            new_line = f"### R{index}. {title}\n"
            changed |= lines[position] != new_line
            lines[position] = new_line
        if changed:
            document.body = "".join(lines)
            self._atomic_write(requirements_path, document.render())
        return {
            "design_path": str(design_path),
            "requirements_path": str(requirements_path),
            "requirement_count": len(headings),
            "changed": changed,
        }

    def verify_design(self, design_file_path: str) -> dict[str, Any]:
        design_path, requirements_path = self.resolve_pair(design_file_path)
        design, requirements, snapshots = self._pair_state(design_path, requirements_path)
        headings = self._requirement_headings(requirements.body)
        if not headings or any(
            found != expected for expected, (_, found, _) in enumerate(headings, 1)
        ):
            raise ValueError("Requirement indices are not current; call design.index")
        results: dict[str, Any] = {}
        differences: list[str] = []
        for name, snapshot in snapshots.items():
            repo = self.resolve_repo_path(name, design_path.parent)
            current = self.generate_tree_sha(repo)
            recorded = snapshot["implementation"] or snapshot["design"]
            matches = current == recorded
            results[str(repo)] = {**snapshot, "current": current, "matches": matches}
            if not matches:
                differences.append(str(repo))
        return {
            "verified": True,
            "design_path": str(design_path),
            "requirements_path": str(requirements_path),
            "status": design.metadata["status"],
            "delegated_review": design.metadata["delegated_review"],
            "requirement_count": len(headings),
            "repositories": results,
            "snapshot_differences": differences,
        }

    def capture_implementation(self, design_file_path: str) -> dict[str, Any]:
        design_path, requirements_path = self.resolve_pair(design_file_path)
        design, requirements, snapshots = self._pair_state(design_path, requirements_path)
        if design.metadata["status"] in {"superseded", "cancelled"}:
            raise ValueError(f"Cannot implement a {design.metadata['status']} design")
        for name, snapshot in snapshots.items():
            snapshot["implementation"] = self.generate_tree_sha(
                self.resolve_repo_path(name, design_path.parent)
            )
        design.metadata.update(status="implemented", repos=snapshots)
        requirements.metadata.update(status="implemented", repos=snapshots)
        original = design_path.read_text(encoding="utf-8")
        self._atomic_write(design_path, design.render())
        try:
            self._atomic_write(requirements_path, requirements.render())
        except Exception:
            self._atomic_write(design_path, original)
            raise
        return {
            "design_path": str(design_path),
            "requirements_path": str(requirements_path),
            "status": "implemented",
            "repositories": snapshots,
        }
