"""Tests for the dependency pipeline watchdog (issue #683).

Every check is exercised through `run_checks` against a synthetic repository tree, so the
tests cover the wiring (which fetcher feeds which check) rather than each helper in
isolation. Network access is replaced by dictionaries keyed on URL.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_dependency_pipeline as watchdog

NOW = datetime(2026, 9, 9, tzinfo=UTC)
SETUP_UV_SHA = "20cfd1bf945f4377ade1205e4dbc17946fc9a30d"
CHECKSUM_URL = watchdog.SETUP_UV_CHECKSUMS.format(sha=SETUP_UV_SHA, filename="known-checksums.ts")
CHECKSUM_JSON_URL = watchdog.SETUP_UV_CHECKSUMS.format(sha=SETUP_UV_SHA, filename="known-checksums.json")


def _pypi(version: str, released_at: datetime) -> dict[str, Any]:
    return {"info": {"version": version}, "urls": [{"upload_time_iso_8601": released_at.isoformat()}]}


def _search_payload(created_at: datetime | None) -> dict[str, Any]:
    if created_at is None:
        return {"items": []}
    return {"items": [{"created_at": created_at.isoformat()}]}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal but faithful copy of the files the watchdog reads."""
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (tmp_path / ".github" / "dependabot.yml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "updates": [
                    {"package-ecosystem": eco, "cooldown": {"default-days": 7}, "labels": ["dependencies", label]}
                    for eco, label in (
                        ("github-actions", "github-actions"),
                        ("uv", "python"),
                        ("pre-commit", "pre-commit"),
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    (workflows / "test.yml").write_text(
        yaml.safe_dump(
            {"jobs": {"test": {"steps": [{"uses": f"astral-sh/setup-uv@{SETUP_UV_SHA}"}]}}},
        ),
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests>=2.34.2"]\n'
        '[project.optional-dependencies]\ndev = ["mypy==2.3.1", "isort==7.0.0"]\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        'name = "requests"\nversion = "2.34.2"\n\nname = "mypy"\nversion = "2.3.1"\n\nname = "isort"\nversion = "7.0.0"\n',
        encoding="utf-8",
    )
    (tmp_path / ".tool-versions").write_text("# comment line\nuv 0.12.4\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def healthy_responses() -> dict[str, Any]:
    recent = NOW - timedelta(days=2)
    return {
        "search:github-actions": _search_payload(recent),
        "search:python": _search_payload(recent),
        "search:pre-commit": _search_payload(recent),
        # requests / mypy are current; isort's newer release is a major bump Dependabot ignores.
        "pypi:requests": _pypi("2.34.2", NOW - timedelta(days=60)),
        "pypi:mypy": _pypi("2.3.1", NOW - timedelta(days=25)),
        "pypi:isort": _pypi("9.0.1", NOW - timedelta(days=30)),
        CHECKSUM_URL: '"x86_64-unknown-linux-gnu-0.12.3":\n"x86_64-unknown-linux-gnu-0.12.4":\n',
        watchdog.DEPENDABOT_UV_DOCKERFILE: "FROM ghcr.io/astral-sh/uv:0.12.7 AS uv\n",
    }


def _fetchers(responses: dict[str, Any]):
    def fetch_json(url: str) -> Any:
        if "/search/issues" in url:
            label = url.split("label:")[1].split("+", maxsplit=1)[0]
            return responses[f"search:{label}"]
        name = url.removeprefix("https://pypi.org/pypi/").removesuffix("/json")
        return responses[f"pypi:{name}"]

    def fetch_text(url: str) -> str:
        if url not in responses:
            response = requests.Response()
            response.status_code = 404
            raise requests.HTTPError(response=response)
        return responses[url]

    return fetch_json, fetch_text


def _run(repo: Path, responses: dict[str, Any], **kwargs: Any) -> dict[str, watchdog.CheckResult]:
    fetch_json, fetch_text = _fetchers(responses)
    options = {"max_pr_age_days": 21, "max_stale_direct": 3, **kwargs}
    results = watchdog.run_checks(fetch_json, fetch_text, repo, "owner/name", NOW, **options)
    return {result.check_id: result for result in results}


def test_healthy_pipeline_passes_every_check(repo: Path, healthy_responses: dict[str, Any]) -> None:
    results = _run(repo, healthy_responses)

    assert set(results) == {"1:github-actions", "1:pre-commit", "1:uv", "2", "3a", "3b", "3c"}
    assert all(result.ok for result in results.values())
    assert results["2"].facts["dependencies"] == []


def test_stalled_ecosystem_and_backlog_are_reported_together(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """The 2026-07-27 outage shape: no uv PR for six weeks while updates piled up."""
    responses = dict(healthy_responses)
    responses["search:python"] = _search_payload(NOW - timedelta(days=44))
    responses["pypi:mypy"] = _pypi("2.4.0", NOW - timedelta(days=20))

    results = _run(repo, responses, max_stale_direct=0)

    assert not results["1:uv"].ok
    assert results["1:uv"].facts["age_days"] == 44
    assert results["1:github-actions"].ok
    assert not results["2"].ok
    assert [item["name"] for item in results["2"].facts["dependencies"]] == ["mypy"]


def test_releases_inside_the_cooldown_are_not_counted_as_stale(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """A release younger than dependabot.yml's cooldown has no PR due yet."""
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi("2.4.0", NOW - timedelta(days=3))

    results = _run(repo, responses, max_stale_direct=0)

    assert results["2"].ok
    assert results["2"].facts["cooldown_days"] == 7


def test_major_bumps_are_not_counted_as_stale(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """dependabot.yml ignores version-update:semver-major, so isort 7 -> 9 is not a miss."""
    results = _run(repo, healthy_responses, max_stale_direct=0)

    assert results["2"].ok


def test_missing_ecosystem_pr_history_is_an_alert(repo: Path, healthy_responses: dict[str, Any]) -> None:
    responses = dict(healthy_responses)
    responses["search:pre-commit"] = _search_payload(None)

    results = _run(repo, responses)

    assert not results["1:pre-commit"].ok
    assert results["1:pre-commit"].facts["last_pr_at"] is None


def test_uv_pin_outside_known_checksums_is_high_severity(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """setup-uv installs an unknown uv without verifying it, so this must not be a warning."""
    (repo / ".tool-versions").write_text("uv 0.12.10\n", encoding="utf-8")

    results = _run(repo, healthy_responses)

    assert not results["3a"].ok
    assert results["3a"].severity == "high"
    assert results["3b"].ok  # 0.12.10 is ahead of the known ceiling, not behind it
    # CLAUDE.md accepts drift inside one minor, so 0.12.10 vs dependabot-core's 0.12.7 is fine.
    assert results["3c"].ok


def test_uv_pin_on_a_different_minor_than_dependabot_is_an_alert(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """Different minors mean the binary writing uv.lock and the one verifying it diverged."""
    responses = dict(healthy_responses)
    responses[watchdog.DEPENDABOT_UV_DOCKERFILE] = "FROM ghcr.io/astral-sh/uv:0.13.0 AS uv\n"

    results = _run(repo, responses)

    assert not results["3c"].ok
    assert results["3c"].severity == "medium"


def test_uv_pin_behind_known_checksums_is_low_severity(repo: Path, healthy_responses: dict[str, Any]) -> None:
    (repo / ".tool-versions").write_text("uv 0.12.3\n", encoding="utf-8")

    results = _run(repo, healthy_responses)

    assert results["3a"].ok
    assert not results["3b"].ok
    assert results["3b"].severity == "low"


def test_checksum_table_falls_back_to_the_json_layout(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """setup-uv#1025 moved the table to JSON; the pinned commit decides which file exists."""
    responses = dict(healthy_responses)
    del responses[CHECKSUM_URL]
    responses[CHECKSUM_JSON_URL] = '{"x86_64-unknown-linux-gnu-0.12.4": "abc"}'

    results = _run(repo, responses)

    assert results["3a"].ok


def test_report_only_contains_structured_facts(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """PR and issue text is untrusted input, so it must never reach the rendered report."""
    responses = dict(healthy_responses)
    responses["search:python"] = {
        "items": [{"created_at": (NOW - timedelta(days=44)).isoformat(), "title": "<!-- injected -->", "body": "evil"}]
    }

    results = watchdog.run_checks(
        *_fetchers(responses), repo, "owner/name", NOW, max_pr_age_days=21, max_stale_direct=3
    )
    report = watchdog.render_report(results, "owner/name", NOW)

    assert "injected" not in report
    assert "evil" not in report
    assert "🚨 1 件の検査が閾値を超えた。" in report


def test_main_writes_both_report_files_and_signals_alerts(
    repo: Path, healthy_responses: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = dict(healthy_responses)
    responses["search:python"] = _search_payload(NOW - timedelta(days=44))
    monkeypatch.setattr(watchdog, "PROJECT_ROOT", repo)
    monkeypatch.setattr(watchdog, "make_fetchers", lambda token: _fetchers(responses))
    report_path, json_path = tmp_path / "report.md", tmp_path / "report.json"

    exit_code = watchdog.main(
        ["--repo", "owner/name", "--report", str(report_path), "--json", str(json_path)],
    )

    assert exit_code == 1
    assert "依存更新パイプラインの生存確認" in report_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert [entry["id"] for entry in payload if not entry["ok"]] == ["1:uv"]


def test_main_reports_healthy_pipelines_with_exit_zero(
    repo: Path, healthy_responses: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watchdog, "PROJECT_ROOT", repo)
    monkeypatch.setattr(watchdog, "make_fetchers", lambda token: _fetchers(healthy_responses))

    assert watchdog.main(["--repo", "owner/name"]) == 0


def test_network_failure_exits_two_rather_than_reporting_healthy(
    repo: Path, healthy_responses: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A watchdog that fails quietly reproduces the outage it exists to detect."""

    def exploding_fetchers(token: str | None):
        def fetch_json(url: str) -> Any:
            raise requests.ConnectionError("boom")

        return fetch_json, lambda url: ""

    monkeypatch.setattr(watchdog, "PROJECT_ROOT", repo)
    monkeypatch.setattr(watchdog, "make_fetchers", exploding_fetchers)

    assert watchdog.main(["--repo", "owner/name"]) == 2
    assert "生存確認を実行できませんでした" in capsys.readouterr().err


def test_main_requires_a_repository(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    assert watchdog.main([]) == 2
    assert "GITHUB_REPOSITORY" in capsys.readouterr().err


def test_watchdog_workflow_uses_least_privilege_and_no_pull_request_target() -> None:
    """issue #683: judging on PR metadata must not need write access to code or PRs."""
    project_root = Path(__file__).resolve().parent.parent
    workflow = yaml.safe_load(
        (project_root / ".github" / "workflows" / "dependency-pipeline-watchdog.yml").read_text(encoding="utf-8")
    )
    # PyYAML parses the unquoted `on:` key as the boolean True.
    triggers = workflow[True]

    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert "pull_request_target" not in triggers
