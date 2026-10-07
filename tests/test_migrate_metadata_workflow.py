""".github/workflows/migrate-metadata.yml の静的テスト.

失敗が赤い run にならない (pipefail なしの tee で CLI の exit を失う、Summary が結果を見ない) 退行と、
入力の run: への直接展開・過剰な権限の退行を検出する。ステップ本体を bash で実行する検査はしない
(実挙動はマージ後の手動 dispatch で確認する)。
"""

from __future__ import annotations

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
