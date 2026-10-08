""".github/workflows/migrate-metadata.yml の静的テスト.

失敗が赤い run にならない (pipefail なしの tee で CLI の exit を失う、Summary が結果を見ない) 退行と、
入力の run: への直接展開・過剰な権限の退行を検出する。ステップ本体を bash で実行する検査はしない
(実挙動はマージ後の手動 dispatch で確認する)。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "migrate-metadata.yml"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _steps(workflow: dict[str, Any], job: str) -> list[dict[str, Any]]:
    return workflow["jobs"][job]["steps"]


def _step(workflow: dict[str, Any], job: str, name: str) -> tuple[int, dict[str, Any]]:
    for index, step in enumerate(_steps(workflow, job)):
        if step.get("name") == name:
            return index, step
    pytest.fail(f"step not found: {job}/{name}")


def _all_run_scripts(workflow: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (f"{job_id}/{step.get('name')}", step["run"])
        for job_id, job in workflow["jobs"].items()
        for step in job["steps"]
        if isinstance(step.get("run"), str)
    ]


def test_every_run_step_uses_bash_with_pipefail(workflow: dict[str, Any]) -> None:
    """shell: bash を明示すると -eo pipefail で動く (既定の bash -e はパイプ途中の失敗を失う)."""
    assert workflow["defaults"] == {"run": {"shell": "bash"}}
    for job in workflow["jobs"].values():
        assert "defaults" not in job
        for step in job["steps"]:
            assert step.get("shell", "bash") == "bash"


def test_run_scripts_do_not_expand_expressions_or_pipe_to_tee(workflow: dict[str, Any]) -> None:
    """入力・ステップ出力は env 経由で渡し、件数はログではなく JSON から取る."""
    scripts = _all_run_scripts(workflow)
    assert scripts
    for name, script in scripts:
        assert "${{" not in script, name
        assert "| tee" not in script, name


def test_write_permissions_are_scoped_to_the_migrate_job(workflow: dict[str, Any]) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["migrate"]["permissions"] == {"contents": "write", "pull-requests": "write"}
    check_job = workflow["jobs"]["check-version-change"]
    assert "write" not in check_job.get("permissions", {}).values()
    _, checkout = _step(workflow, "check-version-change", "Checkout repository")
    assert checkout["with"]["persist-credentials"] is False


def test_dry_run_counts_come_from_output_json(workflow: dict[str, Any]) -> None:
    """raw と processed の両方を dry-run し、件数を --output-json の JSON から取る."""
    _, step = _step(workflow, "migrate", "Run migration (dry-run check)")
    script = step["run"]
    assert script.count("--output-json)") == 2
    assert "--metadata-dir data/processed/.metadata --data-dir data/processed" in script
    assert "jq" in script
    assert "migrated_count=" in script
    assert step["env"]["TARGET_VERSION"] == "${{ steps.params.outputs.target_version }}"


def test_prepare_parameters_reads_inputs_from_env_and_checks_the_version_format(workflow: dict[str, Any]) -> None:
    _, step = _step(workflow, "migrate", "Prepare parameters")
    assert step["env"]["INPUT_TARGET_VERSION"] == "${{ github.event.inputs.target_version }}"
    assert step["env"]["INPUT_DRY_RUN"] == "${{ github.event.inputs.dry_run }}"
    assert step["env"]["DETECTED_VERSION"] == "${{ needs.check-version-change.outputs.new_version }}"
    assert "VERSION_PATTERN='^[0-9]+\\.[0-9]+(\\.[0-9]+)?$'" in step["run"]
    # set -e の下では到達しない "$? の後判定" を使わない
    assert "$?" not in step["run"]


def test_migration_covers_processed_and_is_schema_checked_before_the_pr(workflow: dict[str, Any]) -> None:
    _, migrate = _step(workflow, "migrate", "Run migration")
    assert migrate["id"] == "migrate"
    assert "--metadata-dir data/processed/.metadata --data-dir data/processed" in migrate["run"]

    schema_index, schema_step = _step(workflow, "migrate", "Validate metadata schema")
    pr_index, pr_step = _step(workflow, "migrate", "Create Pull Request")
    assert "scripts/validate_metadata_schema.py" in schema_step["run"]
    assert "--version-profiles tokyo-idsc-raw,tokyo-idsc-processed" in schema_step["run"]
    # 移行対象が 0 件でも本実行なら検査する (「既に目標バージョン」の緑が schema 未検査にならないように)
    assert schema_step["if"] == "steps.params.outputs.dry_run == 'false'"
    assert schema_index < pr_index
    assert "git add -- data/raw/.metadata data/processed/.metadata" in pr_step["run"]


def test_install_dependencies_includes_dev_extras(workflow: dict[str, Any]) -> None:
    """schema 検証の jsonschema は dev extra にある."""
    _, step = _step(workflow, "migrate", "Install dependencies")
    assert step["run"] == "uv sync --all-extras --locked"


def test_summary_reports_failure_from_job_status(workflow: dict[str, Any]) -> None:
    """Summary は job.status を見て失敗を ❌ で表示し、✅ の分岐より先に判定する."""
    _, step = _step(workflow, "migrate", "Summary")
    assert step["if"] == "always()"
    assert step["env"]["JOB_STATUS"] == "${{ job.status }}"
    script = step["run"]
    failure = script.index('if [ "$JOB_STATUS" != "success" ]; then')
    assert failure < script.index('echo "✅')
    assert "❌" in script
    assert "RAW_MIGRATED" in script
    assert "PROCESSED_MIGRATED" in script


CREATE_PR_SCRIPT = WORKFLOW.parent.parent.parent / "scripts" / "create_pr.sh"

FAKE_GH = """#!/usr/bin/env bash
# gh の代わり: pr create の --body-file を記録し、PR の URL を返す。他のサブコマンドは何もしない
if [ "$1" = "pr" ] && [ "$2" = "create" ]; then
  while [ $# -gt 0 ]; do
    if [ "$1" = "--body-file" ]; then cp "$2" "$CAPTURED_BODY"; fi
    shift
  done
  echo "https://github.com/example/repo/pull/1"
fi
"""


def _git(cwd: Path, *args: str, env: dict[str, str]) -> None:
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


def test_migration_pr_body_names_raw_and_processed_metadata(tmp_path: Path) -> None:
    """移行 PR は raw と processed の両方をステージするので、生成される PR 本文も両方を対象として書く."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH, encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    remote, repo = tmp_path / "remote.git", tmp_path / "repo"
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CAPTURED_BODY": str(tmp_path / "body.md"),
        "GITHUB_ENV": str(tmp_path / "github_env"),
        "GITHUB_TOKEN": "dummy",
        "CURRENT_DATE": "2026-01-01",
        "FETCH_TIMESTAMP": "20260101_000000",
        "GITHUB_RUN_ID": "1",
        "TARGET_VERSION": "1.3.0",
        "MIGRATED_COUNT": "2",
        "AUTO_MERGE": "false",
    }
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], env=env, check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], env=env, check=True)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init", env=env)
    _git(repo, "remote", "add", "origin", str(remote), env=env)
    for directory in ("data/raw/.metadata", "data/processed/.metadata"):
        (repo / directory).mkdir(parents=True)
        (repo / directory / "x.json").write_text("{}", encoding="utf-8")
    _git(repo, "add", "--", "data/raw/.metadata", "data/processed/.metadata", env=env)

    # create_pr.sh は本文を固定パス /tmp/pr_body.md に書くので、元から無ければ後片付けする
    pr_body_file = Path("/tmp/pr_body.md")
    existed = pr_body_file.exists()
    try:
        subprocess.run(
            ["bash", str(CREATE_PR_SCRIPT), "migrate-metadata", "メタデータマイグレーション"],
            cwd=repo,
            env=env,
            check=True,
            capture_output=True,
        )
    finally:
        if not existed:
            pr_body_file.unlink(missing_ok=True)

    body = (tmp_path / "body.md").read_text(encoding="utf-8")
    assert "- **対象ディレクトリ**: data/raw/.metadata/, data/processed/.metadata/" in body
    assert "- メタデータは data/raw/.metadata/ と data/processed/.metadata/ ディレクトリに保存されています" in body
