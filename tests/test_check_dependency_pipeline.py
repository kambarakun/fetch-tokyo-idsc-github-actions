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

# A Wednesday, matching the watchdog's own schedule two days after the Monday updater run.
NOW = datetime(2026, 9, 9, tzinfo=UTC)
SETUP_UV_SHA = "20cfd1bf945f4377ade1205e4dbc17946fc9a30d"
CHECKSUM_URL = watchdog.SETUP_UV_CHECKSUMS.format(sha=SETUP_UV_SHA, filename="known-checksums.ts")
CHECKSUM_JSON_URL = watchdog.SETUP_UV_CHECKSUMS.format(sha=SETUP_UV_SHA, filename="known-checksums.json")
CORE_RELEASE_TAG = "v0.395.0"
DOCKERFILE_URL = watchdog.DEPENDABOT_UV_DOCKERFILE.format(ref=CORE_RELEASE_TAG)
CLAUDE_ACTION = "anthropics/claude-code-action"
CLAUDE_ACTION_SHA = "833fb0f8c9f6686b33d963a8bae0a94f4936ab2a"
CLAUDE_ACTION_TAG = "v1.0.220"
CHECK_4 = f"4:{CLAUDE_ACTION}"


def _bun_lock(*versions: str) -> str:
    """A bun.lock excerpt. The scoped sibling is what a naive `shell-quote@` match trips on."""
    entries = "".join(f'    "shell-quote": ["shell-quote@{version}", "", {{}}, "sha512-b"],\n' for version in versions)
    return '    "@types/shell-quote": ["@types/shell-quote@1.7.5", "", {}, "sha512-a"],\n' + entries


def _action_lock_url(ref: str) -> str:
    return watchdog.ACTION_LOCKFILE.format(action=CLAUDE_ACTION, ref=ref, lockfile="bun.lock")


def _releases(*tags: str, prerelease: str | None = None, draft: str | None = None) -> list[dict[str, Any]]:
    """A releases page. `v1` is the floating major tag claude-code-action republishes."""
    entries = [{"tag_name": tag, "prerelease": False, "draft": False} for tag in ("v1", *tags)]
    if prerelease:
        entries.append({"tag_name": prerelease, "prerelease": True, "draft": False})
    if draft:
        entries.append({"tag_name": draft, "prerelease": False, "draft": True})
    return entries


def _pypi(
    *releases: tuple[str, datetime],
    yanked: set[str] | None = None,
    requires_python: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A PyPI payload. `info.version` is the last entry, mirroring "latest"."""
    yanked = yanked or set()
    requires_python = requires_python or {}
    return {
        "info": {"version": releases[-1][0]},
        "releases": {
            version: [
                {
                    "upload_time_iso_8601": released_at.isoformat(),
                    "yanked": version in yanked,
                    "requires_python": requires_python.get(version),
                }
            ]
            for version, released_at in releases
        },
    }


def _pr_payload(created_at: datetime | None) -> list[dict[str, Any]]:
    """A `GET /issues` page. `pull_request` is what marks an entry as a PR rather than an issue."""
    if created_at is None:
        return []
    return [{"created_at": created_at.isoformat(), "pull_request": {"url": "https://example.invalid/1"}}]


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
                    {
                        "package-ecosystem": eco,
                        "cooldown": {"default-days": 7},
                        "labels": ["dependencies", label],
                        "schedule": {
                            "interval": "weekly",
                            "day": "monday",
                            "time": "09:00",
                            "timezone": "Asia/Tokyo",
                        },
                    }
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
    (workflows / "claude.yml").write_text(
        yaml.safe_dump({"jobs": {"claude": {"steps": [{"uses": f"{CLAUDE_ACTION}@{CLAUDE_ACTION_SHA}"}]}}}),
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.11,<3.12"\ndependencies = ["requests>=2.34.2"]\n'
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
        "prs:github-actions": _pr_payload(recent),
        "prs:python": _pr_payload(recent),
        "prs:pre-commit": _pr_payload(recent),
        # requests / mypy are current; isort's newer release is a major bump Dependabot ignores.
        "pypi:requests": _pypi(("2.34.2", NOW - timedelta(days=60))),
        "pypi:mypy": _pypi(("2.3.1", NOW - timedelta(days=25))),
        "pypi:isort": _pypi(("9.0.1", NOW - timedelta(days=30))),
        CHECKSUM_URL: '"x86_64-unknown-linux-gnu-0.12.3":\n"x86_64-unknown-linux-gnu-0.12.4":\n',
        "core-release": {"tag_name": CORE_RELEASE_TAG},
        DOCKERFILE_URL: "FROM ghcr.io/astral-sh/uv:0.12.7 AS uv\n",
        # Today's real state: every release still locks the vulnerable shell-quote (issue #656).
        "action-releases": _releases(CLAUDE_ACTION_TAG),
        _action_lock_url(CLAUDE_ACTION_SHA): _bun_lock("1.8.4"),
        _action_lock_url(CLAUDE_ACTION_TAG): _bun_lock("1.8.4"),
    }


def _fetchers(responses: dict[str, Any]):
    def fetch_json(url: str) -> Any:
        if "/issues?" in url:
            label = url.split("labels=")[1].split("&", maxsplit=1)[0]
            return responses[f"prs:{label}"]
        if url == watchdog.DEPENDABOT_CORE_LATEST_RELEASE:
            return responses["core-release"]
        if url == watchdog.ACTION_RELEASES.format(action=CLAUDE_ACTION):
            return responses["action-releases"]
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

    assert set(results) == {
        "1:github-actions",
        "1:pre-commit",
        "1:uv",
        "2",
        "3a",
        "3b",
        "3c",
        CHECK_4,
    }
    assert all(result.ok for result in results.values())
    assert results["2"].facts["dependencies"] == []


def test_stalled_ecosystem_and_backlog_are_reported_together(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """The 2026-07-27 outage shape: no uv PR for six weeks while updates piled up."""
    responses = dict(healthy_responses)
    responses["prs:python"] = _pr_payload(NOW - timedelta(days=44))
    responses["pypi:mypy"] = _pypi(("2.4.0", NOW - timedelta(days=20)))

    results = _run(repo, responses, max_stale_direct=0)

    assert not results["1:uv"].ok
    assert results["1:uv"].facts["age_days"] == 44
    assert results["1:github-actions"].ok
    assert not results["2"].ok
    assert [item["name"] for item in results["2"].facts["dependencies"]] == ["mypy"]


def test_releases_inside_the_cooldown_are_not_counted_as_stale(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """A release younger than dependabot.yml's cooldown has no PR due yet."""
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(("2.4.0", NOW - timedelta(days=3)))

    results = _run(repo, responses, max_stale_direct=0)

    assert results["2"].ok
    assert results["2"].facts["cooldown_days"] == 7


def test_major_bumps_are_not_counted_as_stale(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """dependabot.yml ignores version-update:semver-major, so isort 7 -> 9 is not a miss."""
    results = _run(repo, healthy_responses, max_stale_direct=0)

    assert results["2"].ok


def test_overdue_release_is_found_behind_a_newer_ineligible_one(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """`info.version` alone hides a backlog on frequently released packages.

    mypy 2.3.1 is locked. 2.4.0 has been overdue for 20 days, but a major 3.0.0 and a
    same-day 2.5.0 both sit above it, so looking only at "latest" would report nothing
    exactly when the updater is stalled.
    """
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(
        ("2.4.0", NOW - timedelta(days=20)),
        ("3.0.0", NOW - timedelta(days=10)),
        ("2.5.0", NOW - timedelta(days=1)),
    )

    results = _run(repo, responses, max_stale_direct=0)

    assert not results["2"].ok
    assert results["2"].facts["dependencies"] == [
        {
            "name": "mypy",
            "locked": "2.3.1",
            "latest": "2.4.0",
            "released_at": (NOW - timedelta(days=20)).isoformat(),
        }
    ]


def test_yanked_and_prerelease_versions_are_not_counted_as_stale(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """Dependabot proposes neither, so counting them would fire during healthy operation."""
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(
        ("2.4.0", NOW - timedelta(days=30)),
        ("2.5.0rc1", NOW - timedelta(days=20)),
        yanked={"2.4.0"},
    )

    results = _run(repo, responses, max_stale_direct=0)

    assert results["2"].ok


def test_releases_dropping_the_declared_python_are_not_counted_as_stale(
    repo: Path, healthy_responses: dict[str, Any]
) -> None:
    """Neither Dependabot nor uv can propose a release that excludes the declared interpreter.

    Counting one would make the backlog grow permanently and eventually open an outage
    issue that no amount of updating could clear.
    """
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(
        ("2.4.0", NOW - timedelta(days=30)),
        requires_python={"2.4.0": ">=3.12"},
    )

    results = _run(repo, responses, max_stale_direct=0)

    assert results["2"].ok


def test_releases_raising_the_python_floor_within_the_same_minor_are_not_counted(
    repo: Path, healthy_responses: dict[str, Any]
) -> None:
    """`requires-python = ">=3.11,<3.12"` covers 3.11.0, not just the runner's patch level.

    A release needing `>=3.11.10` installs fine on the machine running this watchdog, yet uv
    cannot lock it for the whole declared range, so Dependabot never proposes it. Judging by
    the running interpreter would leave it in the backlog forever.
    """
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(
        ("2.4.0", NOW - timedelta(days=30)),
        requires_python={"2.4.0": ">=3.11.10"},
    )

    results = _run(repo, responses, max_stale_direct=0)

    assert results["2"].ok


def test_releases_capping_python_inside_the_declared_range_are_not_counted(
    repo: Path, healthy_responses: dict[str, Any]
) -> None:
    """Narrowing the range from above is as unresolvable as narrowing it from below."""
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(
        ("2.4.0", NOW - timedelta(days=30)),
        requires_python={"2.4.0": ">=3.11,<3.11.5"},
    )

    results = _run(repo, responses, max_stale_direct=0)

    assert results["2"].ok


def test_releases_excluding_a_python_inside_the_declared_range_are_not_counted(
    repo: Path, healthy_responses: dict[str, Any]
) -> None:
    """A `!=` hole leaves an interpreter the project declares without a resolvable release."""
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(
        ("2.4.0", NOW - timedelta(days=30)),
        requires_python={"2.4.0": ">=3.11,!=3.11.4"},
    )

    results = _run(repo, responses, max_stale_direct=0)

    assert results["2"].ok


def test_releases_excluding_the_declared_python_series_are_not_counted(
    repo: Path, healthy_responses: dict[str, Any]
) -> None:
    """A wildcard exclusion is a valid Requires-Python and must not crash the comparison.

    `SpecifierSet.contains("3.11.*")` raises, so a release declaring `!=3.11.*` used to take
    the whole run down with it -- a watchdog that dies on ordinary upstream metadata is the
    silent failure this check exists to catch.
    """
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(
        ("2.4.0", NOW - timedelta(days=30)),
        requires_python={"2.4.0": ">=3.6,!=3.11.*"},
    )

    results = _run(repo, responses, max_stale_direct=0)

    assert results["2"].ok


@pytest.mark.parametrize("requires_python", [">=3.11", ">=3.9,<3.12", ">=3.9,<=3.12", ">=3.9,!=3.10.*"])
def test_releases_covering_the_declared_python_range_are_still_counted(
    repo: Path, healthy_responses: dict[str, Any], requires_python: str
) -> None:
    """The range comparison must not swallow releases Dependabot really could propose."""
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(
        ("2.4.0", NOW - timedelta(days=30)),
        requires_python={"2.4.0": requires_python},
    )

    results = _run(repo, responses, max_stale_direct=0)

    assert not results["2"].ok
    assert [item["name"] for item in results["2"].facts["dependencies"]] == ["mypy"]


def test_cooldown_is_judged_at_the_last_scheduled_updater_run(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """The watchdog runs on Wednesday; the updater last ran on Monday 09:00 JST.

    A release published 8 days before Wednesday was only 6 days old on Monday, so it was
    still inside the 7-day cooldown when Dependabot last looked. Judging it against "now"
    would report a stall that has not happened.
    """
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(("2.4.0", NOW - timedelta(days=8)))

    results = _run(repo, responses, max_stale_direct=0)

    assert results["2"].ok
    # ...while a release that was already eligible on Monday is still reported.
    responses["pypi:mypy"] = _pypi(("2.4.0", NOW - timedelta(days=11)))
    assert not _run(repo, responses, max_stale_direct=0)["2"].ok


def test_duplicate_requirements_consume_one_backlog_slot(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """A package listed in both `dependencies` and an extra is still one Dependabot proposal."""
    (repo / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests>=2.34.2", "mypy>=2.3.1"]\n'
        '[project.optional-dependencies]\ndev = ["mypy==2.3.1"]\ndocs = ["mypy==2.3.1"]\n',
        encoding="utf-8",
    )
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = _pypi(("2.4.0", NOW - timedelta(days=30)))

    results = _run(repo, responses, max_stale_direct=1)

    assert results["2"].facts["count"] == 1
    assert results["2"].ok


def test_unparsable_checksum_table_fails_loudly(
    repo: Path, healthy_responses: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 that parses to nothing is a layout change, not "this uv is unverified"."""
    responses = dict(healthy_responses)
    responses[CHECKSUM_URL] = "export const KNOWN_CHECKSUMS = {};"
    monkeypatch.setattr(watchdog, "PROJECT_ROOT", repo)
    monkeypatch.setattr(watchdog, "make_fetchers", lambda token: _fetchers(responses))

    assert watchdog.main(["--repo", "owner/name"]) == 2


def test_report_write_failure_exits_two_rather_than_signalling_an_alert(
    repo: Path, healthy_responses: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 1 means "threshold exceeded"; an unwritable report must not be mistaken for one."""
    monkeypatch.setattr(watchdog, "PROJECT_ROOT", repo)
    monkeypatch.setattr(watchdog, "make_fetchers", lambda token: _fetchers(healthy_responses))
    unwritable = tmp_path / "missing-directory" / "report.md"

    assert watchdog.main(["--repo", "owner/name", "--report", str(unwritable)]) == 2


def test_missing_release_history_fails_loudly(
    repo: Path, healthy_responses: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PyPI may drop `releases`; reporting an empty backlog then would be a silent failure."""
    responses = dict(healthy_responses)
    responses["pypi:mypy"] = {"info": {"version": "2.4.0"}}
    monkeypatch.setattr(watchdog, "PROJECT_ROOT", repo)
    monkeypatch.setattr(watchdog, "make_fetchers", lambda token: _fetchers(responses))

    assert watchdog.main(["--repo", "owner/name"]) == 2


def test_the_workflow_token_never_leaves_the_github_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """The token carries write scope; pypi.org and raw.githubusercontent.com must not see it."""
    seen: dict[str, dict[str, str]] = {}

    class _Response:
        ok = True

        def json(self) -> Any:
            return {}

        @property
        def text(self) -> str:
            return ""

    def fake_get(url: str, headers: dict[str, str], timeout: int) -> _Response:
        seen[url] = headers
        return _Response()

    monkeypatch.setattr(watchdog.requests, "get", fake_get)
    fetch_json, fetch_text = watchdog.make_fetchers("secret-token")

    fetch_json(f"{watchdog.GITHUB_API}/repos/owner/name/issues?labels=python")
    fetch_json(watchdog.PYPI_JSON.format(name="mypy"))
    fetch_text(DOCKERFILE_URL)
    fetch_text(_action_lock_url(CLAUDE_ACTION_TAG))

    authorized = {url for url, headers in seen.items() if "Authorization" in headers}
    assert authorized == {f"{watchdog.GITHUB_API}/repos/owner/name/issues?labels=python"}
    assert len(seen) == 4


def test_http_get_puts_the_response_body_into_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """issue #697: a bare status code hid whether 403 was a missing permission or a rate limit."""

    class _Forbidden:
        ok = False
        status_code = 403
        reason = "Forbidden"
        text = '{"message": "Resource not accessible by integration"}'

    monkeypatch.setattr(watchdog.requests, "get", lambda url, headers, timeout: _Forbidden())
    fetch_json, _ = watchdog.make_fetchers("secret-token")

    with pytest.raises(requests.HTTPError) as excinfo:
        fetch_json(f"{watchdog.GITHUB_API}/repos/owner/name/issues?labels=python")

    assert "403 Forbidden" in str(excinfo.value)
    assert "Resource not accessible by integration" in str(excinfo.value)


def test_missing_ecosystem_pr_history_is_an_alert(repo: Path, healthy_responses: dict[str, Any]) -> None:
    responses = dict(healthy_responses)
    responses["prs:pre-commit"] = _pr_payload(None)

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
    responses[DOCKERFILE_URL] = "FROM ghcr.io/astral-sh/uv:0.13.0 AS uv\n"

    results = _run(repo, responses)

    assert not results["3c"].ok
    assert results["3c"].severity == "medium"
    # The report names the revision compared against: no public source identifies the
    # revision GitHub has deployed, so a human has to be able to judge the remaining lag.
    assert results["3c"].facts["bundled_ref"] == CORE_RELEASE_TAG


def test_the_bundled_uv_is_read_from_a_release_not_from_main(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """`main` can carry an unreleased or since-reverted bump the hosted updater never ran."""
    responses = dict(healthy_responses)
    responses["core-release"] = {"tag_name": "v0.400.0"}
    responses[watchdog.DEPENDABOT_UV_DOCKERFILE.format(ref="v0.400.0")] = "FROM ghcr.io/astral-sh/uv:0.12.7 AS uv\n"
    responses[watchdog.DEPENDABOT_UV_DOCKERFILE.format(ref="main")] = "FROM ghcr.io/astral-sh/uv:0.13.0 AS uv\n"

    results = _run(repo, responses)

    assert results["3c"].ok
    assert results["3c"].facts["bundled"] == "0.12.7"
    assert results["3c"].facts["bundled_ref"] == "v0.400.0"


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


def test_a_bundled_cve_without_a_fixed_release_is_not_an_alert(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """Issue #656's accepted risk. Alerting every week would keep the tracking issue open forever."""
    results = _run(repo, healthy_responses)

    result = results[CHECK_4]
    assert result.ok
    assert result.facts["pinned_version"] == "1.8.4"
    assert result.facts["latest_version"] == "1.8.4"
    # The waiting state has to stay readable in the report, not just in the verdict.
    assert CLAUDE_ACTION_TAG in result.detail


def test_a_still_vulnerable_upstream_bump_is_reported_with_its_own_version(
    repo: Path, healthy_responses: dict[str, Any]
) -> None:
    """Upstream can move the copy without clearing the advisory (1.8.2 -> 1.8.5, fixed in 1.9.0).

    The verdict is unchanged -- there is still nothing to move to -- but the report is what a
    human reads before checking the lockfile by hand, so it has to name both versions.
    """
    responses = dict(healthy_responses)
    responses[_action_lock_url(CLAUDE_ACTION_SHA)] = _bun_lock("1.8.2")
    responses[_action_lock_url(CLAUDE_ACTION_TAG)] = _bun_lock("1.8.5")

    result = _run(repo, responses)[CHECK_4]

    assert result.ok
    assert result.facts["pinned_version"] == "1.8.2"
    assert result.facts["latest_version"] == "1.8.5"
    assert "1.8.2" in result.detail
    assert "1.8.5" in result.detail


def test_a_fixed_action_release_makes_the_bundled_cve_actionable(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """The one moment issue #656 is waiting for: upstream regenerates its lockfile."""
    responses = dict(healthy_responses)
    responses[_action_lock_url(CLAUDE_ACTION_TAG)] = _bun_lock("1.10.0")

    result = _run(repo, responses)[CHECK_4]

    assert not result.ok
    assert result.severity == "high"
    assert result.facts == {
        "action": CLAUDE_ACTION,
        "package": "shell-quote",
        "fixed_in": "1.9.0",
        "advisory": "GHSA-395f-4hp3-45gv",
        "pinned_sha": CLAUDE_ACTION_SHA,
        "pinned_version": "1.8.4",
        "latest_release": CLAUDE_ACTION_TAG,
        "latest_version": "1.10.0",
    }


def test_a_pin_past_the_fix_clears_the_bundled_cve(repo: Path, healthy_responses: dict[str, Any]) -> None:
    responses = dict(healthy_responses)
    responses[_action_lock_url(CLAUDE_ACTION_SHA)] = _bun_lock("1.10.0")
    responses[_action_lock_url(CLAUDE_ACTION_TAG)] = _bun_lock("1.10.0")

    assert _run(repo, responses)[CHECK_4].ok


def test_a_dropped_package_is_green_but_says_so_in_its_own_words(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """Dropping the package clears this advisory, but a renamed fork would carry the same bug.

    The verdict stays green -- alerting on the successful outcome would be noise -- so the
    report line is what has to tell a human to re-check the row rather than read as "we are
    on a fixed version".
    """
    responses = dict(healthy_responses)
    responses[_action_lock_url(CLAUDE_ACTION_SHA)] = (
        '    "@types/shell-quote": ["@types/shell-quote@1.7.5", "", {}, "sha512-a"],\n'
    )

    result = _run(repo, responses)[CHECK_4]

    assert result.ok
    assert result.facts["pinned_version"] is None
    assert "テーブルの妥当性を確認する" in result.detail
    assert "修正版" not in result.detail


def test_the_newest_action_release_is_neither_the_floating_tag_nor_lexicographic(
    repo: Path, healthy_responses: dict[str, Any]
) -> None:
    """`/releases/latest` answers `v1` here, and "v1.0.9" sorts above "v1.0.220" as a string."""
    responses = dict(healthy_responses)
    responses["action-releases"] = _releases("v1.0.9", CLAUDE_ACTION_TAG, prerelease="v2.0.0-rc.1", draft="v2.0.0")
    responses[_action_lock_url("v1.0.9")] = _bun_lock("1.10.0")

    result = _run(repo, responses)[CHECK_4]

    # Reading v1 or v1.0.9 would have raised (no lockfile) or reported the wrong version.
    assert result.facts["latest_release"] == CLAUDE_ACTION_TAG
    assert result.facts["latest_version"] == "1.8.4"


def test_the_lowest_real_copy_in_the_lockfile_decides_exposure(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """A scoped sibling must not be read as the package, and a duplicate copy must not hide it."""
    responses = dict(healthy_responses)
    responses[_action_lock_url(CLAUDE_ACTION_SHA)] = _bun_lock("1.10.0", "1.8.4")

    result = _run(repo, responses)[CHECK_4]

    assert result.facts["pinned_version"] == "1.8.4"


def test_a_moved_action_lockfile_fails_loudly(
    repo: Path, healthy_responses: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treating a 404 as "the package is gone" would be an all-clear the check never verified."""
    responses = dict(healthy_responses)
    del responses[_action_lock_url(CLAUDE_ACTION_TAG)]
    monkeypatch.setattr(watchdog, "PROJECT_ROOT", repo)
    monkeypatch.setattr(watchdog, "make_fetchers", lambda token: _fetchers(responses))

    assert watchdog.main(["--repo", "owner/name"]) == 2


def test_an_action_whose_name_ends_in_the_watched_one_is_not_counted_as_a_pin_of_it(
    repo: Path, healthy_responses: dict[str, Any]
) -> None:
    """`not-anthropics/claude-code-action` is a different Action, not a second pin of this one."""
    (repo / ".github" / "workflows" / "lookalike.yml").write_text(
        yaml.safe_dump({"jobs": {"other": {"steps": [{"uses": f"not-{CLAUDE_ACTION}@{'c' * 40}"}]}}}),
        encoding="utf-8",
    )

    assert _run(repo, healthy_responses)[CHECK_4].facts["pinned_sha"] == CLAUDE_ACTION_SHA


def test_a_watched_action_pinned_to_two_commits_fails_loudly(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """Half-applied bumps, and a watched Action that was removed, both have to be visible."""
    (repo / ".github" / "workflows" / "claude-review.yml").write_text(
        yaml.safe_dump({"jobs": {"review": {"steps": [{"uses": f"{CLAUDE_ACTION}@{'b' * 40}"}]}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one pinned"):
        _run(repo, healthy_responses)


def test_report_only_contains_structured_facts(repo: Path, healthy_responses: dict[str, Any]) -> None:
    """PR and issue text is untrusted input, so it must never reach the rendered report."""
    responses = dict(healthy_responses)
    responses["prs:python"] = [
        {
            "created_at": (NOW - timedelta(days=44)).isoformat(),
            "pull_request": {"url": "https://example.invalid/1"},
            "title": "<!-- injected -->",
            "body": "evil",
        }
    ]

    results = watchdog.run_checks(
        *_fetchers(responses),
        repo,
        "owner/name",
        NOW,
        max_pr_age_days=21,
        max_stale_direct=3,
    )
    report = watchdog.render_report(results, "owner/name", NOW)

    assert "injected" not in report
    assert "evil" not in report
    assert "🚨 1 件の検査が閾値を超えた。" in report


def test_main_writes_both_report_files_and_signals_alerts(
    repo: Path, healthy_responses: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = dict(healthy_responses)
    responses["prs:python"] = _pr_payload(NOW - timedelta(days=44))
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
    """issue #683: judging on PR metadata must not need write access to code or PRs.

    issue #697: `pull-requests: read` is required, not optional. Check 1 reads the pull requests
    that `GET /repos/{repo}/issues` returns, and without the permission the token cannot see them
    at all, so every ecosystem looks dead and check 1 reports an outage that is not happening.
    """
    project_root = Path(__file__).resolve().parent.parent
    workflow = yaml.safe_load(
        (project_root / ".github" / "workflows" / "dependency-pipeline-watchdog.yml").read_text(encoding="utf-8")
    )
    # PyYAML parses the unquoted `on:` key as the boolean True.
    triggers = workflow[True]

    assert workflow["permissions"] == {"contents": "read", "issues": "write", "pull-requests": "read"}
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert "pull_request_target" not in triggers
