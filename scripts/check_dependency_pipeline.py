#!/usr/bin/env python3
"""Liveness check for the dependency update pipeline (issue #683).

The uv-ecosystem outage that started on 2026-07-27 (issue #681) was invisible for six
weekly cycles: Dependabot aborted before ``uv lock`` and opened no PR, and "no PR" is
indistinguishable from "nothing to update" when you only look at the repository. The
failure lived in job logs only the repository owner can read, and Dependabot Security
Updates run through the same updater, so CVE fixes were stalled too.

These checks turn that silence into a signal. Every input is a structured field --
timestamps, labels, version strings -- so PR and issue text never reaches the report
(AGENTS.md treats that text as untrusted input).

Exit codes: 0 healthy, 1 at least one alert, 2 the check itself could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
import yaml
from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

PROJECT_ROOT = Path(__file__).resolve().parent.parent

GITHUB_API = "https://api.github.com"
PYPI_JSON = "https://pypi.org/pypi/{name}/json"
SETUP_UV_CHECKSUMS = "https://raw.githubusercontent.com/astral-sh/setup-uv/{sha}/src/download/checksum/{filename}"
DEPENDABOT_UV_DOCKERFILE = "https://raw.githubusercontent.com/dependabot/dependabot-core/main/uv/Dockerfile"

# setup-uv moved the table from a generated .ts module to plain JSON on 2026-08-19
# (astral-sh/setup-uv#1025). The pinned commit decides which one exists, so try both.
CHECKSUM_FILENAMES = ("known-checksums.json", "known-checksums.ts")
# ubuntu-latest runners, i.e. the platform whose checksum actually gates CI.
CHECKSUM_PLATFORM = "x86_64-unknown-linux-gnu"
CHECKSUM_KEY = re.compile(rf'"{CHECKSUM_PLATFORM}-(?P<version>\d+\.\d+\.\d+)"')
# Same shape astral-sh/setup-uv accepts for `version-file: .tool-versions`: a bare
# `uv <version>` line, no trailing comment (see tests/test_dependabot_config.py).
TOOL_VERSIONS_UV_LINE = re.compile(r"^\s*uv\s*v?\s*(?P<version>\S+)\s*$")
SETUP_UV_REF = re.compile(r"astral-sh/setup-uv@(?P<sha>[0-9a-f]{40})")
DEPENDABOT_UV_IMAGE = re.compile(r"ghcr\.io/astral-sh/uv:(?P<version>\d+\.\d+\.\d+)")

# Three missed weekly cycles. The 2026-07-27 outage would have tripped this on 2026-08-17,
# five weeks before a human noticed it.
DEFAULT_MAX_PR_AGE_DAYS = 21
# Steady state is 0-2: a release inside the cooldown window is not yet actionable and is
# excluded below, so anything above this means proposals are not landing.
DEFAULT_MAX_STALE_DIRECT = 3

Severity = str
FetchJson = Callable[[str], Any]
FetchText = Callable[[str], str]


@dataclass(frozen=True)
class CheckResult:
    """One verdict plus the structured facts that produced it."""

    check_id: str
    title: str
    severity: Severity
    ok: bool
    detail: str
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StaleDependency:
    """A direct dependency whose newer release is past the Dependabot cooldown."""

    name: str
    locked: str
    latest: str
    released_at: datetime


def _http_get(url: str, token: str | None, accept: str) -> requests.Response:
    headers = {"Accept": accept, "User-Agent": "fetch-tokyo-idsc-dependency-watchdog"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response


def make_fetchers(token: str | None) -> tuple[FetchJson, FetchText]:
    """Build the two network accessors; tests substitute them wholesale."""

    def fetch_json(url: str) -> Any:
        return _http_get(url, token, "application/vnd.github+json").json()

    def fetch_text(url: str) -> str:
        return _http_get(url, token, "text/plain").text

    return fetch_json, fetch_text


def dependabot_ecosystems(root: Path) -> dict[str, str]:
    """Map each configured ecosystem to the label that identifies its PRs.

    `uv` PRs are labelled `python`, so the label is the only reliable join key between
    dependabot.yml and the PRs it produces.
    """
    config = yaml.safe_load((root / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    ecosystems: dict[str, str] = {}
    for update in config["updates"]:
        labels = [label for label in update.get("labels", []) if label != "dependencies"]
        if labels:
            ecosystems[update["package-ecosystem"]] = labels[0]
    return ecosystems


def cooldown_days(root: Path) -> int:
    """Read the shared cooldown so the stale-dependency window never drifts from config."""
    config = yaml.safe_load((root / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    return max(update.get("cooldown", {}).get("default-days", 0) for update in config["updates"])


def last_dependabot_pr(fetch_json: FetchJson, repo: str, label: str) -> datetime | None:
    """Creation time of the newest Dependabot PR carrying `label`, or None if there is none."""
    query = f"repo:{repo}+type:pr+label:{label}+author:app/dependabot"
    payload = fetch_json(f"{GITHUB_API}/search/issues?q={query}&sort=created&order=desc&per_page=1")
    items = payload.get("items", [])
    if not items:
        return None
    return datetime.fromisoformat(items[0]["created_at"])


def direct_requirements(root: Path) -> list[Requirement]:
    """Every dependency Dependabot can propose, i.e. the ones written in pyproject.toml."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    specs: list[str] = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)
    return [Requirement(spec) for spec in specs]


def locked_versions(root: Path) -> dict[str, str]:
    lock_text = (root / "uv.lock").read_text(encoding="utf-8")
    return dict(re.findall(r'name = "([^"]+)"\nversion = "([^"]+)"', lock_text))


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def stale_direct_dependencies(
    fetch_json: FetchJson,
    requirements: Iterable[Requirement],
    locked: dict[str, str],
    now: datetime,
    cooldown: int,
) -> list[StaleDependency]:
    """Direct dependencies Dependabot should already have proposed but has not.

    Major bumps are skipped because dependabot.yml ignores `version-update:semver-major`,
    and releases younger than the cooldown are skipped because their PR is not due yet.
    Counting either would make the check fire during healthy operation.
    """
    stale: list[StaleDependency] = []
    for requirement in requirements:
        current = locked.get(_canonical(requirement.name))
        if current is None:
            continue
        payload = fetch_json(PYPI_JSON.format(name=requirement.name))
        latest = payload["info"]["version"]
        try:
            current_version, latest_version = Version(current), Version(latest)
        except InvalidVersion:  # pragma: no cover - defensive; PyPI versions are PEP 440
            continue
        if latest_version <= current_version or latest_version.major != current_version.major:
            continue
        uploads = [url["upload_time_iso_8601"] for url in payload.get("urls", []) if url.get("upload_time_iso_8601")]
        if not uploads:
            continue
        released_at = min(datetime.fromisoformat(upload) for upload in uploads)
        if (now - released_at).days < cooldown:
            continue
        stale.append(StaleDependency(requirement.name, current, latest, released_at))
    return stale


def tool_versions_uv_pin(root: Path) -> Version:
    lines = (root / ".tool-versions").read_text(encoding="utf-8").splitlines()
    pins = [
        match["version"]
        for line in lines
        if not line.lstrip().startswith("#") and (match := TOOL_VERSIONS_UV_LINE.match(line))
    ]
    if len(pins) != 1:
        raise ValueError(f"expected exactly one uv pin in .tool-versions, found {len(pins)}")
    return Version(pins[0])


def setup_uv_pinned_sha(root: Path) -> str:
    """The single setup-uv commit every workflow pins (guarded by test_dependabot_config)."""
    refs = {
        match["sha"]
        for workflow in sorted((root / ".github" / "workflows").glob("*.yml"))
        for match in SETUP_UV_REF.finditer(workflow.read_text(encoding="utf-8"))
    }
    if len(refs) != 1:
        raise ValueError(f"expected exactly one pinned setup-uv commit, found {len(refs)}")
    return next(iter(refs))


def known_uv_checksums(fetch_text: FetchText, sha: str) -> set[Version]:
    """uv versions the pinned setup-uv can verify; anything else installs unverified."""
    for filename in CHECKSUM_FILENAMES:
        try:
            body = fetch_text(SETUP_UV_CHECKSUMS.format(sha=sha, filename=filename))
        except requests.HTTPError:
            continue
        return {Version(match["version"]) for match in CHECKSUM_KEY.finditer(body)}
    raise ValueError(f"no known-checksums file found for setup-uv commit {sha}")


def dependabot_bundled_uv(fetch_text: FetchText) -> Version:
    """The uv image dependabot-core ships, i.e. the binary that rewrites uv.lock."""
    match = DEPENDABOT_UV_IMAGE.search(fetch_text(DEPENDABOT_UV_DOCKERFILE))
    if match is None:
        raise ValueError("could not read the uv version from dependabot-core's uv/Dockerfile")
    return Version(match["version"])


def check_pr_age(
    fetch_json: FetchJson, repo: str, ecosystems: dict[str, str], now: datetime, max_age_days: int
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for ecosystem, label in sorted(ecosystems.items()):
        created_at = last_dependabot_pr(fetch_json, repo, label)
        if created_at is None:
            results.append(
                CheckResult(
                    f"1:{ecosystem}",
                    f"{ecosystem} エコシステムの最終 Dependabot PR",
                    "high",
                    False,
                    f"`{label}` ラベルの Dependabot PR が 1 件も存在しない",
                    {"ecosystem": ecosystem, "label": label, "last_pr_at": None},
                )
            )
            continue
        age_days = (now - created_at).days
        results.append(
            CheckResult(
                f"1:{ecosystem}",
                f"{ecosystem} エコシステムの最終 Dependabot PR",
                "high",
                age_days <= max_age_days,
                f"最終 PR から {age_days} 日経過 (閾値 {max_age_days} 日 / {created_at.date().isoformat()})",
                {
                    "ecosystem": ecosystem,
                    "label": label,
                    "last_pr_at": created_at.isoformat(),
                    "age_days": age_days,
                    "threshold_days": max_age_days,
                },
            )
        )
    return results


def check_stale_dependencies(stale: Sequence[StaleDependency], cooldown: int, threshold: int) -> CheckResult:
    listing = ", ".join(f"{item.name} {item.locked} -> {item.latest}" for item in stale) or "なし"
    return CheckResult(
        "2",
        "cooldown を過ぎた直接依存の滞留",
        "high",
        len(stale) <= threshold,
        f"{len(stale)} 件 (閾値 {threshold} 件 / cooldown {cooldown} 日): {listing}",
        {
            "count": len(stale),
            "threshold": threshold,
            "cooldown_days": cooldown,
            "dependencies": [
                {
                    "name": item.name,
                    "locked": item.locked,
                    "latest": item.latest,
                    "released_at": item.released_at.isoformat(),
                }
                for item in stale
            ],
        },
    )


def check_uv_pin(pinned: Version, known: set[Version], bundled: Version, setup_uv_sha: str) -> list[CheckResult]:
    """3a/3b/3c from issue #683.

    The pin is deliberately behind upstream uv: setup-uv installs an unknown version
    *without verifying its checksum*, so the ceiling is what the pinned setup-uv knows,
    not what astral released (CLAUDE.md "uv 本体の更新経路").
    """
    highest = max(known, default=None)
    return [
        CheckResult(
            "3a",
            "uv pin が setup-uv の既知 checksum に含まれる",
            "high",
            pinned in known,
            f".tool-versions の uv {pinned} は setup-uv {setup_uv_sha[:7]} の既知 checksum に"
            + ("含まれる" if pinned in known else "**含まれない** (CI が未検証バイナリを導入している)"),
            {"pinned": str(pinned), "setup_uv_sha": setup_uv_sha, "known_count": len(known)},
        ),
        CheckResult(
            "3b",
            "検証つきで追随できる uv がある",
            "low",
            highest is None or pinned >= highest,
            f"既知 checksum の上限は {highest} / pin は {pinned}",
            {"pinned": str(pinned), "highest_known": str(highest) if highest else None},
        ),
        CheckResult(
            "3c",
            "Dependabot 同梱 uv との系列一致",
            "medium",
            (pinned.major, pinned.minor) == (bundled.major, bundled.minor),
            f"pin {pinned} / dependabot-core 同梱 {bundled} (major.minor の一致を要求)",
            {"pinned": str(pinned), "bundled": str(bundled)},
        ),
    ]


def run_checks(
    fetch_json: FetchJson,
    fetch_text: FetchText,
    root: Path,
    repo: str,
    now: datetime,
    max_pr_age_days: int,
    max_stale_direct: int,
) -> list[CheckResult]:
    cooldown = cooldown_days(root)
    results = check_pr_age(fetch_json, repo, dependabot_ecosystems(root), now, max_pr_age_days)
    stale = stale_direct_dependencies(fetch_json, direct_requirements(root), locked_versions(root), now, cooldown)
    results.append(check_stale_dependencies(stale, cooldown, max_stale_direct))
    setup_uv_sha = setup_uv_pinned_sha(root)
    results.extend(
        check_uv_pin(
            tool_versions_uv_pin(root),
            known_uv_checksums(fetch_text, setup_uv_sha),
            dependabot_bundled_uv(fetch_text),
            setup_uv_sha,
        )
    )
    return results


SEVERITY_MARK = {"high": "🔴", "medium": "🟡", "low": "🟢"}


def render_report(results: Sequence[CheckResult], repo: str, now: datetime) -> str:
    alerts = [result for result in results if not result.ok]
    lines = [
        f"依存更新パイプラインの生存確認 ({repo} / {now.isoformat(timespec='seconds')})",
        "",
        ("✅ 全ての検査を通過した。" if not alerts else f"🚨 {len(alerts)} 件の検査が閾値を超えた。"),
        "",
        "| | 検査 | 重要度 | 結果 |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        mark = "✅" if result.ok else "🚨"
        lines.append(
            f"| {mark} | {result.title} | {SEVERITY_MARK[result.severity]} {result.severity} | {result.detail} |"
        )
    if alerts:
        lines += ["", "## 対応", ""]
        lines.append("`docs/dependency-pipeline.md` の「アラート別の対応」を参照する。")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--max-pr-age-days", type=int, default=DEFAULT_MAX_PR_AGE_DAYS)
    parser.add_argument("--max-stale-direct", type=int, default=DEFAULT_MAX_STALE_DIRECT)
    parser.add_argument("--report", type=Path, help="Markdown レポートの出力先")
    parser.add_argument("--json", type=Path, help="構造化結果の出力先")
    args = parser.parse_args(argv)

    if not args.repo:
        print("--repo または GITHUB_REPOSITORY が必要です", file=sys.stderr)
        return 2

    fetch_json, fetch_text = make_fetchers(os.environ.get("GITHUB_TOKEN"))
    now = datetime.now(UTC)
    try:
        results = run_checks(
            fetch_json, fetch_text, PROJECT_ROOT, args.repo, now, args.max_pr_age_days, args.max_stale_direct
        )
    except (requests.RequestException, ValueError, KeyError) as error:
        # A watchdog that fails quietly reproduces the very bug it exists to catch,
        # so surface this as a red run rather than as "healthy".
        print(f"生存確認を実行できませんでした: {error}", file=sys.stderr)
        return 2

    report = render_report(results, args.repo, now)
    print(report, end="")
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    if args.json:
        payload = [
            {
                "id": result.check_id,
                "title": result.title,
                "severity": result.severity,
                "ok": result.ok,
                "detail": result.detail,
                "facts": result.facts,
            }
            for result in results
        ]
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
