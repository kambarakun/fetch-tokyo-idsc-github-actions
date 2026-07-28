import tomllib
from importlib.metadata import version
from pathlib import Path

import requests
import yaml


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


def test_requests_uses_bundled_type_information():
    project_root = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    pre_commit = yaml.safe_load((project_root / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    lock_text = (project_root / "uv.lock").read_text(encoding="utf-8")

    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]
    mypy_hook = next(hook for repo in pre_commit["repos"] for hook in repo["hooks"] if hook["id"] == "mypy")

    assert not any(dependency.startswith("types-requests") for dependency in dev_dependencies)
    assert "types-requests" not in mypy_hook["additional_dependencies"]
    assert f"requests=={version('requests')}" in mypy_hook["additional_dependencies"]
    assert 'name = "types-requests"' not in lock_text
    assert (Path(requests.__file__).parent / "py.typed").is_file()
