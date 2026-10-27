"""Secret scanning through the gitleaks pre-commit hook (issue #736).

The integration tests run the real hook from .pre-commit-config.yaml in a scratch git repository,
so the first run installs the pinned gitleaks hook environment into the pre-commit cache (a clone
from GitHub and a Go build; the data source is never contacted). Synthetic tokens are assembled at
run time so no secret-shaped literal exists in this file.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import string
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRE_COMMIT_CONFIG = PROJECT_ROOT / ".pre-commit-config.yaml"
SCRIPT = PROJECT_ROOT / "scripts" / "check_secrets.py"
GITLEAKS_REPO = "https://github.com/gitleaks/gitleaks"


def _synthetic_github_token() -> str:
    alphabet = string.ascii_letters + string.digits
    return "ghp" + "_" + "".join(secrets.choice(alphabet) for _ in range(36))


def _load_pre_commit_config() -> dict:
    return yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repository whose only hook is the project's gitleaks hook, with the same top-level settings."""
    # Keep the user's global git config (signing, hooks path) out of setup, pre-commit and the commit.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    _git(tmp_path, "init", "-q")

    config = _load_pre_commit_config()
    config["repos"] = [repo for repo in config["repos"] if repo["repo"] == GITLEAKS_REPO]
    (tmp_path / ".pre-commit-config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    for relative in ("scripts/check_secrets.py", ".gitleaks.toml", ".gitleaksignore"):
        if (PROJECT_ROOT / relative).exists():
            (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT_ROOT / relative, tmp_path / relative)
    _write(tmp_path, "README.md", "# scratch\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _write(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pre_commit(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pre_commit", *args], cwd=repo, check=False, capture_output=True, text=True
    )


def _commit(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "commit", "-m", "change"], cwd=repo, check=False, capture_output=True, text=True)


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def test_commit_with_a_staged_secret_is_rejected_without_printing_it(repo: Path) -> None:
    # Arrange
    token = _synthetic_github_token()
    assert _pre_commit(repo, "install").returncode == 0
    _write(repo, "src/settings.py", f'GITHUB_TOKEN = "{token}"\n')
    _git(repo, "add", "src/settings.py")

    # Act
    result = _commit(repo)

    # Assert
    assert result.returncode != 0
    assert "src/settings.py:1: github-pat" in _output(result)
    assert token not in _output(result)
    assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1"


def test_commit_scans_the_staged_version_not_the_working_tree(repo: Path) -> None:
    # Arrange
    token = _synthetic_github_token()
    assert _pre_commit(repo, "install").returncode == 0
    _write(repo, "src/settings.py", f'GITHUB_TOKEN = "{token}"\n')
    _git(repo, "add", "src/settings.py")
    _write(repo, "src/settings.py", 'GITHUB_TOKEN = ""\n')

    # Act
    result = _commit(repo)

    # Assert
    assert result.returncode != 0
    assert "src/settings.py:1: github-pat" in _output(result)
    assert token not in _output(result)
    assert (repo / "src/settings.py").read_text(encoding="utf-8") == 'GITHUB_TOKEN = ""\n'


def test_commit_ignores_a_secret_that_stays_unstaged(repo: Path) -> None:
    # Arrange
    token = _synthetic_github_token()
    assert _pre_commit(repo, "install").returncode == 0
    _write(repo, "src/settings.py", 'GITHUB_TOKEN = ""\n')
    _git(repo, "add", "src/settings.py")
    _write(repo, "src/settings.py", f'GITHUB_TOKEN = "{token}"\n')

    # Act
    result = _commit(repo)

    # Assert
    assert result.returncode == 0, _output(result)
    assert _git(repo, "show", "HEAD:src/settings.py") == 'GITHUB_TOKEN = ""\n'
    assert token in (repo / "src/settings.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    ["src/settings.py", "data/logs/run.json", "data/raw/sample_2025_01.csv", "tests/fixtures/response.json"],
)
def test_all_files_run_detects_a_committed_secret_with_nothing_staged(repo: Path, relative: str) -> None:
    """data/ and tests/fixtures/ are outside every other hook's targets, but not gitleaks'."""
    # Arrange
    token = _synthetic_github_token()
    _write(repo, relative, f'token,"{token}"\n')
    _git(repo, "add", relative)
    _git(repo, "commit", "-q", "-m", "secret committed without hooks")

    # Act
    result = _pre_commit(repo, "run", "gitleaks", "--all-files")

    # Assert
    assert result.returncode != 0
    assert f"{relative}:1: github-pat" in _output(result)
    assert "1 candidate secret(s) in 1 file(s)" in _output(result)
    assert token not in _output(result)


def test_all_files_run_passes_on_clean_files(repo: Path) -> None:
    # Arrange
    _write(repo, "src/settings.py", 'GITHUB_TOKEN_ENV = "GITHUB_TOKEN"\n')
    _write(repo, "data/raw/sample_2025_01.csv", "week,count\n1,0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "clean")

    # Act
    result = _pre_commit(repo, "run", "gitleaks", "--all-files", "--verbose")

    # Assert
    assert result.returncode == 0, _output(result)
    tracked = len(_git(repo, "ls-files").splitlines())
    assert f"scanned {tracked} file(s)" in _output(result)
    assert "no secrets found" in _output(result)


def test_scanner_error_fails_the_hook(repo: Path) -> None:
    # Arrange
    _write(repo, ".gitleaks.toml", "[extend\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "broken scanner config")

    # Act
    result = _pre_commit(repo, "run", "gitleaks", "--all-files")

    # Assert
    assert result.returncode != 0
    assert "check_secrets: gitleaks failed with exit code" in _output(result)
    assert "no secrets found" not in _output(result)


def test_missing_gitleaks_fails_instead_of_passing(tmp_path: Path) -> None:
    # Arrange
    _write(tmp_path, "app.py", "x = 1\n")

    # Act
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "app.py"],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path / "empty-bin")},
        check=False,
        capture_output=True,
        text=True,
    )

    # Assert
    assert result.returncode == 2
    assert "gitleaks is not on PATH" in result.stderr


@pytest.mark.parametrize(
    ("fake_gitleaks", "message"),
    [
        pytest.param("exit 0\n", "gitleaks wrote no readable report", id="clean-exit-without-report"),
        pytest.param(
            'while [ "$1" != "--report-path" ]; do shift; done\necho \'[{"File": "app.py"}]\' > "$2"\nexit 0\n',
            "gitleaks exit code 0 does not match its report",
            id="clean-exit-with-findings",
        ),
        pytest.param(
            'while [ "$1" != "--report-path" ]; do shift; done\necho "[]" > "$2"\nexit 3\n',
            "gitleaks exit code 3 does not match its report",
            id="leaks-exit-without-findings",
        ),
    ],
)
def test_untrustworthy_gitleaks_result_fails(tmp_path: Path, fake_gitleaks: str, message: str) -> None:
    """A gitleaks whose exit code and report disagree (e.g. changed CLI semantics) is never read as clean."""
    # Arrange
    _write(tmp_path, "app.py", "x = 1\n")
    bin_dir = tmp_path / "bin"
    _write(bin_dir, "gitleaks", "#!/bin/sh\n" + fake_gitleaks)
    (bin_dir / "gitleaks").chmod(0o755)

    # Act
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "app.py"],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(bin_dir)},
        check=False,
        capture_output=True,
        text=True,
    )

    # Assert
    assert result.returncode == 2
    assert f"check_secrets: {message}" in result.stderr
    assert "no secrets found" not in result.stdout


def test_no_files_is_a_clean_no_op(tmp_path: Path) -> None:
    # Act
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, check=False, capture_output=True, text=True)

    # Assert
    assert result.returncode == 0
    assert result.stdout == "check_secrets: no files to scan\n"


def test_only_gitleaks_reads_data_fixtures_and_lock_files() -> None:
    """A top-level `exclude` applies to every hook and no hook can override it, so it must not return."""
    config = _load_pre_commit_config()
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}
    generated = ["data/raw/sample_2025_01.csv", "data/logs/run.json", "tests/fixtures/response.json", "uv.lock"]

    assert config.get("exclude", "^$") == "^$"
    assert "exclude" not in hooks["gitleaks"]
    assert hooks["gitleaks"]["entry"] == "python3 scripts/check_secrets.py"
    for hook_id, hook in hooks.items():
        if hook_id != "gitleaks":
            assert all(
                re.search(hook.get("exclude", "^$"), path) for path in generated
            ), f"hook {hook_id!r} needs `exclude: *repo_exclude` in .pre-commit-config.yaml"
