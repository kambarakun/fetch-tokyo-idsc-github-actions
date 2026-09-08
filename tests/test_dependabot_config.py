import re
import subprocess
import sys
import tomllib
from pathlib import Path

import requests
import yaml
from packaging.requirements import Requirement

# Python tools whose pre-commit hooks must run from the uv-managed project venv (issue #680).
PROJECT_VENV_TOOLS = {"black", "isort", "mypy", "ruff"}
# Upstream hook repositories for those tools. Referencing any of them reintroduces a second
# version source that Dependabot's pre-commit ecosystem bumps independently of uv.lock.
UPSTREAM_HOOK_REPOS = {
    "https://github.com/PyCQA/isort",
    "https://github.com/psf/black",
    "https://github.com/psf/black-pre-commit-mirror",
    "https://github.com/astral-sh/ruff-pre-commit",
    "https://github.com/pre-commit/mirrors-mypy",
}
# The pinned uv version must live in a file uv itself never reads (issue #681).
# `[tool.uv] required-version` is a hard guard: Dependabot runs its own bundled uv, so any mismatch
# aborted every uv-ecosystem job (security updates included) before `uv lock`, and did so silently.
UV_VERSION_FILE = ".tool-versions"
# Same pattern astral-sh/setup-uv applies to `version-file: .tool-versions`
# (src/version/tool-versions-file.ts): full-line comments only, no trailing comment on the uv line.
TOOL_VERSIONS_UV_LINE = re.compile(r"^\s*uv\s*v?\s*(?P<version>\S+)\s*$")


def test_all_dependabot_version_updates_have_seven_day_cooldown():
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / ".github" / "dependabot.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    invalid_ecosystems = [
        update["package-ecosystem"]
        for update in config["updates"]
        if update.get("cooldown", {}).get("default-days") != 7
    ]

    assert invalid_ecosystems == []


def test_python_tool_hooks_run_from_project_environment() -> None:
    """pyproject.toml + uv.lock must stay the single version source for the Python tools.

    Before issue #680 the same tools were also pinned by `rev:` in .pre-commit-config.yaml, so
    every Dependabot bump on either side broke CI until the other side was updated by hand.
    """
    project_root = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    pre_commit = yaml.safe_load((project_root / ".pre-commit-config.yaml").read_text(encoding="utf-8"))

    pinned_specifiers = {
        requirement.name: str(requirement.specifier)
        for dependency in pyproject["project"]["optional-dependencies"]["dev"]
        if (requirement := Requirement(dependency)).name in PROJECT_VENV_TOOLS
    }
    hooks = {
        hook["id"]: (repo["repo"], hook)
        for repo in pre_commit["repos"]
        for hook in repo["hooks"]
        if hook["id"] in PROJECT_VENV_TOOLS
    }

    assert set(pinned_specifiers) == PROJECT_VENV_TOOLS
    assert all(specifier.startswith("==") for specifier in pinned_specifiers.values())
    assert set(hooks) == PROJECT_VENV_TOOLS
    for tool, (repo_url, hook) in hooks.items():
        assert repo_url == "local"
        assert hook["language"] == "system"
        assert hook["entry"].split()[:6] == ["uv", "run", "--locked", "--extra", "dev", tool]
        assert "additional_dependencies" not in hook
    assert not any(repo["repo"] in UPSTREAM_HOOK_REPOS for repo in pre_commit["repos"])


def test_requests_uses_bundled_type_information(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    pre_commit = yaml.safe_load((project_root / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    lock_text = (project_root / "uv.lock").read_text(encoding="utf-8")
    invalid_requests_usage = tmp_path / "invalid_requests_usage.py"
    invalid_requests_usage.write_text("import requests\nrequests.get(123)\n", encoding="utf-8")

    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]
    mypy_hook = next(hook for repo in pre_commit["repos"] for hook in repo["hooks"] if hook["id"] == "mypy")

    assert not any(dependency.startswith("types-requests") for dependency in dev_dependencies)
    # The mypy hook runs inside the project venv, so requests' own py.typed is what it sees;
    # a separately pinned copy in additional_dependencies would drift from uv.lock.
    assert "additional_dependencies" not in mypy_hook
    assert 'name = "types-requests"' not in lock_text
    assert (Path(requests.__file__).parent / "py.typed").is_file()

    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-error-summary", str(invalid_requests_usage)],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert 'Argument 1 to "get" has incompatible type "int"' in result.stdout


def test_uv_version_pin_lives_outside_uv_config() -> None:
    """mise and setup-uv must read the uv pin from .tool-versions; uv itself must not see it.

    `[tool.uv] required-version` (added by #606) stopped every uv-ecosystem Dependabot PR from
    2026-07-27 until issue #681 moved the pin, because Dependabot's bundled uv never matched it.
    """
    project_root = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    tool_versions_lines = (project_root / UV_VERSION_FILE).read_text(encoding="utf-8").splitlines()
    uv_pins = [
        match["version"]
        for line in tool_versions_lines
        if not line.lstrip().startswith("#") and (match := TOOL_VERSIONS_UV_LINE.match(line))
    ]
    setup_uv_version_files = {
        f"{workflow.name}:{job_id}": step.get("with", {})
        for workflow in sorted((project_root / ".github" / "workflows").glob("*.yml"))
        for job_id, job in yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"].items()
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    }

    assert "required-version" not in pyproject.get("tool", {}).get("uv", {})
    # uv.toml is optional for other uv settings but must not carry the pin either.
    if (uv_toml := project_root / "uv.toml").exists():
        assert "required-version" not in tomllib.loads(uv_toml.read_text(encoding="utf-8"))
    assert len(uv_pins) == 1
    assert re.fullmatch(r"\d+\.\d+\.\d+", uv_pins[0])
    # mise prefers .mise.toml over .tool-versions in the same directory: keeping both would
    # reintroduce a second pin source.
    assert not any((project_root / name).exists() for name in ("mise.toml", ".mise.toml"))
    # Without `version-file`, setup-uv searches uv.toml then pyproject.toml and otherwise installs
    # the latest uv; an explicit `version` input would silently override the file.
    assert setup_uv_version_files
    assert {key: inputs.get("version-file") for key, inputs in setup_uv_version_files.items()} == dict.fromkeys(
        setup_uv_version_files, UV_VERSION_FILE
    )
    assert not any("version" in inputs for inputs in setup_uv_version_files.values())
