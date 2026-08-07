from __future__ import annotations

from pathlib import Path

import pytest
from mcp_server_design.documents import DesignStore, Settings
from mcp_server_design.reviews import ReviewStore


def make_reviews(tmp_path: Path) -> tuple[ReviewStore, Path]:
    plan = tmp_path / "plan"
    design_dir = plan / "design"
    design_dir.mkdir(parents=True)
    design = design_dir / "design-20260731-1.md"
    design.write_text("---\ntitle: Test\n---\n# Test\n", encoding="utf-8")
    return ReviewStore(DesignStore(Settings(plan))), design


def test_write_review_persists_incrementing_markdown_reports(tmp_path: Path) -> None:
    reviews, design = make_reviews(tmp_path)
    first = reviews.write_review(
        design.name, content="# Review\n\nApproved.", reviewer="conformance"
    )
    second = reviews.write_review(design.name, content="# Review\n\nBlocked.")

    assert set(first) == {"design_filename", "review_number", "review_path"}
    assert set(second) == {"design_filename", "review_number", "review_path"}
    assert first["design_filename"] == design.name
    assert first["review_number"] == 1
    assert second["review_number"] == 2

    first_path = Path(first["review_path"])
    second_path = Path(second["review_path"])
    assert first_path.is_absolute()
    assert first_path.name == "design-20260731-1-1.md"
    assert second_path.name == "design-20260731-1-2.md"
    assert first_path.parent == reviews.designs.settings.reviews_dir

    first_text = first_path.read_text(encoding="utf-8")
    assert first_text.startswith(
        f"---\ndesign: ../design/{design.name}\nreviewer: conformance\n---\n\n"
    )
    assert first_text.endswith("# Review\n\nApproved.\n")
    second_text = second_path.read_text(encoding="utf-8")
    assert second_text.startswith(f"---\ndesign: ../design/{design.name}\n---\n\n")
    assert "reviewer" not in second_text.split("---", 2)[1]


def test_write_review_continues_after_highest_existing_increment(tmp_path: Path) -> None:
    reviews, design = make_reviews(tmp_path)
    directory = reviews.designs.settings.reviews_dir
    directory.mkdir()
    (directory / "design-20260731-1-4.md").write_text("older\n", encoding="utf-8")
    (directory / "design-20260731-9-1.md").write_text("other design\n", encoding="utf-8")

    written = reviews.write_review(design.name, content="Fresh review.")

    assert written["review_number"] == 5
    assert Path(written["review_path"]).name == "design-20260731-1-5.md"


def test_write_review_validates_inputs(tmp_path: Path) -> None:
    reviews, design = make_reviews(tmp_path)

    with pytest.raises(ValueError, match="content must be a non-empty string"):
        reviews.write_review(design.name, content="  ")
    with pytest.raises(ValueError, match="reviewer must be a non-empty string"):
        reviews.write_review(design.name, content="report", reviewer=" ")
    with pytest.raises(PermissionError, match="must be inside"):
        reviews.write_review("notes.md", content="report")
    with pytest.raises(FileNotFoundError, match="Design document not found"):
        reviews.write_review(str(design.parent / "design-20260731-2.md"), content="report")
    assert not (reviews.designs.settings.reviews_dir / "notes.md").exists()


def test_write_review_refuses_non_directory_reviews_path(tmp_path: Path) -> None:
    reviews, design = make_reviews(tmp_path)
    blocker = reviews.designs.settings.reviews_dir
    blocker.write_text("occupied\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a regular directory"):
        reviews.write_review(design.name, content="report")
