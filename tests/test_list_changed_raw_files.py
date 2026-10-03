from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "list_changed_raw_files.sh"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Keep the user's global git config (signing, hooks, renames) out of both setup and the script.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    _git(tmp_path, "init", "-q")
    return tmp_path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _run(repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), base], cwd=repo, check=False, capture_output=True, text=True)


def test_lists_raw_csvs_added_or_changed_since_base(repo: Path) -> None:
    # Arrange
    for name in ("unstaged", "staged", "committed", "renamed", "rewritten", "deleted"):
        _write(repo, f"data/raw/{name}_2025_01.csv", f"{name},1\n")
    _write(repo, "data/raw/.metadata/unstaged_2025_01.json", "{}\n")
    base = _commit_all(repo, "base")

    _write(repo, "data/raw/committed_2025_01.csv", "committed,2\n")
    _git(repo, "mv", "data/raw/renamed_2025_01.csv", "data/raw/renamed_2025_02.csv")
    _commit_all(repo, "after base")
    _write(repo, "data/raw/unstaged_2025_01.csv", "unstaged,2\n")
    _write(repo, "data/raw/staged_2025_01.csv", "staged,2\n")
    _git(repo, "add", "data/raw/staged_2025_01.csv")
    _write(repo, "data/raw/new_2025_01.csv", "new,1\n")
    _write(repo, "data/raw/.metadata/nested_2025_01.csv", "nested,1\n")
    _write(repo, "data/raw/.metadata/unstaged_2025_01.json", '{"changed": true}\n')
    _write(repo, "data/raw/rewritten_2025_01.csv", "rewritten,1\n")
    (repo / "data/raw/deleted_2025_01.csv").unlink()

    # Act
    result = _run(repo, base)

    # Assert
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "data/raw/committed_2025_01.csv",
        "data/raw/new_2025_01.csv",
        "data/raw/renamed_2025_02.csv",
        "data/raw/staged_2025_01.csv",
        "data/raw/unstaged_2025_01.csv",
    ]


def test_prints_nothing_when_raw_is_unchanged(repo: Path) -> None:
    # Arrange
    _write(repo, "data/raw/x_2025_01.csv", "x,1\n")
    base = _commit_all(repo, "base")
    _write(repo, "README.md", "unrelated\n")

    # Act
    result = _run(repo, base)

    # Assert
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("base", ["", "0123456789abcdef0123456789abcdef01234567"])
def test_rejects_missing_or_unknown_base(repo: Path, base: str) -> None:
    # Arrange
    _write(repo, "data/raw/x_2025_01.csv", "x,1\n")
    _commit_all(repo, "base")

    # Act
    result = _run(repo, base)

    # Assert
    assert result.returncode == 2
    assert result.stdout == ""
