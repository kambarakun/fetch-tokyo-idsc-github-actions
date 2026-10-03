#!/usr/bin/env python3
"""Pre-merge vetting of Dependabot pull requests (issue #762).

Dependabot opens up to fifteen version-update PRs every Monday across three ecosystems
(github-actions / uv / pre-commit). CI proves the bump builds; nothing checked whether the
proposed version itself is a landmine -- yanked from PyPI, covered by an advisory, dropping
the Python floor, or pinned to a SHA its tag does not resolve to. This script asks those
questions of public APIs and prints one table per PR with a verdict of OK / WARN / BLOCK.

Versions are read by comparing whole files at the merge base and the head (uv.lock,
workflows, .pre-commit-config.yaml), never from the PR title or body: #749 was titled
"from v3.8.5 to 3.9.8" while its `rev:` said v3.9.8, and PR text is untrusted input that
must not reach a report agents read (AGENTS.md).

Exit codes: 0 no BLOCK (WARN allowed), 1 at least one BLOCK, 2 the vetting itself failed.
A network or parse failure is always 2 -- "could not check" is never reported as OK or BLOCK.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import requests
import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

GITHUB_API = "https://api.github.com"
GITHUB_API_HOST = "api.github.com"
PYPI_RELEASE = "https://pypi.org/pypi/{name}/{version}/json"
PYPI_PROJECT = "https://pypi.org/pypi/{name}/json"
OSV_QUERY = "https://api.osv.dev/v1/query"
DEPENDABOT_AUTHOR = "dependabot[bot]"
ECOSYSTEM_BY_PREFIX = {
    "dependabot/github_actions/": "github-actions",
    "dependabot/uv/": "uv",
    "dependabot/pre_commit/": "pre-commit",
}
EXPECTED_FILES = {
    "uv": ("pyproject.toml", "uv.lock"),
    # GitHub accepts both extensions for workflow files.
    "github-actions": (".github/workflows/*.yml", ".github/workflows/*.yaml"),
    "pre-commit": (".pre-commit-config.yaml",),
}
# The dependabot.yml ecosystem whose cooldown applies to each kind of bump.
ECOSYSTEM_BY_KIND = {"pypi": "uv", "action": "github-actions", "pre-commit": "pre-commit"}
SUPERSEDED_WINDOW_DAYS = 7
GREEN_CONCLUSIONS = {"success", "skipped", "neutral"}
# In CI this job is itself an in-progress check run on the head commit.
SELF_CHECK_NAME = "vet"
# GitHub's documented default when `cooldown.default-days` is absent.
DEFAULT_COOLDOWN_DAYS = 3
PAGE_SIZE = 100

# A trailing subpath (`github/codeql-action/init@...`) still names the `owner/repo` that owns the tag.
# The version comment is optional: a bare SHA pin is still a bump, vetted as "no tag to compare".
USES_PATTERN = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w.-]+)(?:/[\w./-]+)?@([0-9a-f]{40})(?:\s*#\s*(v?\d[\w.-]*))?")
# Report cells are plain text: escape what could open a link, image, code span, HTML or a new cell.
MARKDOWN_SPECIAL = re.compile(r"([\\|`\[\]<])")
SHORT_SHA = 12
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
GITHUB_URL_PATTERN = re.compile(r"github\.com/([^/]+/[^/]+?)(?:\.git)?/?$")
REMOTE_PATTERN = re.compile(r"github\.com[:/]([^/]+/[^/.]+)")

FetchJson = Callable[[str], Any]
FetchText = Callable[[str], str]
PostJson = Callable[[str, dict[str, Any]], Any]
Fetchers = tuple[FetchJson, FetchText, PostJson]


class NotDependabotPullRequestError(Exception):
    """Raised for a PR Dependabot did not author; vetting a human PR would mislabel it."""


@dataclass(frozen=True)
class Bump:
    name: str
    old: str | None
    new: str
    kind: str  # "pypi" | "action" | "pre-commit"
    # The new SHA of a workflow `uses:` or of a SHA-pinned pre-commit `rev:`.
    sha: str | None = None


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    dependency: str
    verdict: str
    detail: str
    links: list[str] = field(default_factory=list)


@dataclass
class PullRequestVerdict:
    number: int
    ecosystem: str | None
    head_sha: str
    bumps: list[Bump]
    checks: list[CheckResult]

    @property
    def verdict(self) -> str:
        verdicts = {check.verdict for check in self.checks}
        return next((level for level in ("BLOCK", "WARN") if level in verdicts), "OK")


# --- network -----------------------------------------------------------------------------


def _authorized_headers(url: str, token: str | None, accept: str) -> dict[str, str]:
    headers = {"Accept": accept, "User-Agent": "fetch-tokyo-idsc-dependabot-vetting"}
    # Same host gate as scripts/check_dependency_pipeline.py: the fetchers also call pypi.org
    # and api.osv.dev, so the credential is tied to the host rather than to the caller.
    if token and urlsplit(url).hostname == GITHUB_API_HOST:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _raise_for_status(response: requests.Response, url: str) -> requests.Response:
    if not response.ok:
        detail = " ".join(response.text.split())[:200]
        raise requests.HTTPError(f"{response.status_code} {response.reason} for {url}: {detail}", response=response)
    return response


def _http_get(url: str, token: str | None, accept: str) -> requests.Response:
    response = requests.get(url, headers=_authorized_headers(url, token, accept), timeout=30)
    return _raise_for_status(response, url)


def _http_post(url: str, token: str | None, payload: dict[str, Any]) -> requests.Response:
    headers = _authorized_headers(url, token, "application/json")
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    return _raise_for_status(response, url)


def make_fetchers(token: str | None) -> Fetchers:
    """Build the three network accessors; tests substitute them wholesale."""

    def fetch_json(url: str) -> Any:
        return _http_get(url, token, "application/vnd.github+json").json()

    def fetch_text(url: str) -> str:
        return _http_get(url, token, "application/vnd.github.raw+json").text

    def post_json(url: str, payload: dict[str, Any]) -> Any:
        return _http_post(url, token, payload).json()

    return fetch_json, fetch_text, post_json


def make_poster(token: str | None) -> PostJson:
    def post(url: str, payload: dict[str, Any]) -> Any:
        return _http_post(url, token, payload).json()

    return post


def _osv_key(payload: dict[str, Any]) -> str:
    package = payload["package"]
    return f"{package['ecosystem']}/{package['name']}@{payload.get('version', '*')}"


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.HTTPError) and response is not None and response.status_code == 404


def _not_found(url: str) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = 404
    response.url = url
    return requests.HTTPError(f"404 Not Found for {url}", response=response)


def _trim_for_fixture(url: str, payload: Any) -> Any:
    """Drop free text and bulk the script never reads, keeping the fixture small and inert."""
    path = urlsplit(url).path
    if re.search(r"/pulls/\d+$", path):
        return {key: value for key, value in payload.items() if key != "body"}
    if "/compare/" in path or re.search(r"/pulls/\d+/files$", path):
        files = payload.get("files", []) if isinstance(payload, dict) else payload
        for item in files:
            item.pop("patch", None)
        return payload
    if path.endswith("/releases"):
        return [
            {key: release.get(key) for key in ("tag_name", "published_at", "draft", "prerelease")}
            for release in payload
        ]
    if urlsplit(url).hostname == "pypi.org":
        # The release time is the earliest upload and a release counts as yanked when every
        # file is, so one synthetic file per release preserves both answers.
        info = payload.get("info") or {}
        trimmed: dict[str, Any] = {
            "info": {key: info.get(key) for key in ("yanked", "yanked_reason", "requires_python")}
        }
        if "urls" in payload:
            trimmed["urls"] = [_compact_files(payload["urls"])] if payload["urls"] else []
        if "releases" in payload:
            trimmed["releases"] = {
                version: [_compact_files(files)] if files else [] for version, files in payload["releases"].items()
            }
        return trimmed
    return payload


def _compact_files(files: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "upload_time_iso_8601": min(item["upload_time_iso_8601"] for item in files),
        "yanked": all(item.get("yanked", False) for item in files),
        "packagetype": files[0].get("packagetype"),
    }


def make_recording_fetchers(token: str | None, record_dir: Path) -> Fetchers:
    """Live fetchers that also write every successful response into a replayable fixture."""
    live_json, live_text, live_post = make_fetchers(token)
    record_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, str] = {}

    def save(key: str, payload: Any, *, raw: bool) -> None:
        if key in index:
            return
        slug = re.sub(r"[^A-Za-z0-9]+", "-", key.split(" ", 1)[1])[-60:].strip("-")
        filename = f"{len(index):03d}-{slug}.{'txt' if raw else 'json'}"
        target = record_dir / filename
        if raw:
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        index[key] = filename
        (record_dir / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")

    def fetch_json(url: str) -> Any:
        payload = _trim_for_fixture(url, live_json(url))
        save(f"GET {url}", payload, raw=False)
        return payload

    def fetch_text(url: str) -> str:
        text = live_text(url)
        save(f"GET {url}", text, raw=True)
        return text

    def post_json(url: str, payload: dict[str, Any]) -> Any:
        response = live_post(url, payload)
        save(f"POST {url} {_osv_key(payload)}", response, raw=False)
        return response

    return fetch_json, fetch_text, post_json


def make_fixture_fetchers(fixture_dir: Path) -> Fetchers:
    """Replay a recorded fixture offline; anything not recorded answers 404, like the live API."""
    index: dict[str, str] = json.loads((fixture_dir / "index.json").read_text(encoding="utf-8"))

    def load(key: str, url: str) -> Any:
        if key not in index:
            raise _not_found(url)
        return (fixture_dir / index[key]).read_text(encoding="utf-8")

    def fetch_json(url: str) -> Any:
        return json.loads(load(f"GET {url}", url))

    def fetch_text(url: str) -> str:
        return str(load(f"GET {url}", url))

    def post_json(url: str, payload: dict[str, Any]) -> Any:
        return json.loads(load(f"POST {url} {_osv_key(payload)}", url))

    return fetch_json, fetch_text, post_json


def _memoize(fetch: Callable[[str], Any]) -> Callable[[str], Any]:
    """Several checks resolve the same tag or release; ask the network once per URL."""
    cache: dict[str, Any] = {}

    def cached(url: str) -> Any:
        if url not in cache:
            try:
                cache[url] = fetch(url)
            except requests.HTTPError as exc:
                if not _is_not_found(exc):
                    raise
                cache[url] = exc
        if isinstance(cache[url], requests.HTTPError):
            raise cache[url]
        return cache[url]

    return cached


def _get_or_none(fetch_json: FetchJson, url: str) -> Any:
    """404 is a signal (no tag, no release); every other failure aborts with exit 2."""
    try:
        return fetch_json(url)
    except requests.HTTPError as exc:
        if _is_not_found(exc):
            return None
        raise


def _paged(fetch_json: FetchJson, url: str, key: str | None = None) -> list[Any]:
    items: list[Any] = []
    page = 1
    while True:
        payload = fetch_json(f"{url}{'&' if '?' in url else '?'}per_page={PAGE_SIZE}&page={page}")
        batch = payload[key] if key else payload
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            return items
        page += 1


# --- pull request input --------------------------------------------------------------------


def load_pull_request(fetch_json: FetchJson, repo: str, number: int) -> dict[str, Any]:
    return fetch_json(f"{GITHUB_API}/repos/{repo}/pulls/{number}")


def changed_files(fetch_json: FetchJson, repo: str, number: int) -> list[str]:
    return [item["filename"] for item in _paged(fetch_json, f"{GITHUB_API}/repos/{repo}/pulls/{number}/files")]


def pull_request_commits(fetch_json: FetchJson, repo: str, number: int) -> list[dict[str, Any]]:
    return _paged(fetch_json, f"{GITHUB_API}/repos/{repo}/pulls/{number}/commits")


def merge_base(fetch_json: FetchJson, repo: str, base_sha: str, head_sha: str) -> str:
    return fetch_json(f"{GITHUB_API}/repos/{repo}/compare/{base_sha}...{head_sha}")["merge_base_commit"]["sha"]


def file_at(fetch_text: FetchText, repo: str, path: str, ref: str) -> str | None:
    try:
        return fetch_text(f"{GITHUB_API}/repos/{repo}/contents/{quote(path)}?ref={ref}")
    except requests.HTTPError as exc:
        if _is_not_found(exc):
            return None
        raise


def detect_ecosystem(head_ref: str) -> str | None:
    return next((eco for prefix, eco in ECOSYSTEM_BY_PREFIX.items() if head_ref.startswith(prefix)), None)


def open_dependabot_prs(fetch_json: FetchJson, repo: str) -> list[int]:
    pulls = _paged(fetch_json, f"{GITHUB_API}/repos/{repo}/pulls?state=open")
    return sorted(pull["number"] for pull in pulls if pull["user"]["login"] == DEPENDABOT_AUTHOR)


# --- parsers -------------------------------------------------------------------------------


def _parse_version(text: str | None) -> Version | None:
    if text is None:
        return None
    try:
        return Version(text.removeprefix("v"))
    except InvalidVersion:
        return None


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _version_key(text: str) -> tuple[int, Version | str]:
    parsed = _parse_version(text)
    return (1, parsed) if parsed is not None else (0, text)


def _pair_bumps(before: dict[str, set[str]], after: dict[str, set[str]], kind: str) -> list[Bump]:
    bumps: list[Bump] = []
    for name in sorted(after):
        old_versions = before.get(name, set())
        old = max(old_versions, key=_version_key) if old_versions else None
        bumps.extend(Bump(name, old, new, kind) for new in sorted(after[name] - old_versions, key=_version_key))
    return bumps


def _lock_versions(text: str | None) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    for package in tomllib.loads(text or "").get("package", []):
        # Only registry packages exist on PyPI; the project itself is `editable` / `virtual`.
        if "registry" in package.get("source", {}):
            versions.setdefault(package["name"], set()).add(package["version"])
    return versions


def uv_lock_bumps(before: str | None, after: str | None) -> list[Bump]:
    return _pair_bumps(_lock_versions(before), _lock_versions(after), "pypi")


def _workflow_pins(text: str | None) -> dict[str, dict[str, str]]:
    pins: dict[str, dict[str, str]] = {}
    for line in (text or "").splitlines():
        if match := USES_PATTERN.match(line):
            pins.setdefault(match[1], {})[match[2]] = match[3] or match[2][:SHORT_SHA]
    return pins


def workflow_bumps(before: str | None, after: str | None) -> list[Bump]:
    old_pins, new_pins = _workflow_pins(before), _workflow_pins(after)
    bumps: list[Bump] = []
    for name in sorted(new_pins):
        old_by_sha = old_pins.get(name, {})
        old = max(old_by_sha.values(), key=_version_key) if old_by_sha else None
        bumps.extend(
            Bump(name, old, version, "action", sha=sha)
            for sha, version in new_pins[name].items()
            if sha not in old_by_sha
        )
    return _dedupe(bumps)


def _pre_commit_revs(text: str | None) -> dict[str, tuple[str, str | None]]:
    if not text:
        return {}
    revs: dict[str, tuple[str, str | None]] = {}
    for entry in yaml.safe_load(text).get("repos", []):
        rev = entry.get("rev")
        if rev is None:  # `local` and `meta` repositories carry no version
            continue
        rev = str(rev)
        comment = re.search(rf"rev:\s*['\"]?{re.escape(rev)}['\"]?\s*#\s*(?:frozen:\s*)?(v?\d[\w.-]*)", text)
        revs[entry["repo"]] = (rev, comment[1] if comment else None)
    return revs


def pre_commit_bumps(before: str | None, after: str | None) -> list[Bump]:
    old_revs, new_revs = _pre_commit_revs(before), _pre_commit_revs(after)
    bumps: list[Bump] = []
    for repo_url, (rev, comment) in sorted(new_revs.items()):
        if old_revs.get(repo_url, (None, None))[0] == rev:
            continue
        match = GITHUB_URL_PATTERN.search(repo_url)
        name = match[1] if match else repo_url
        old_rev, old_comment = old_revs.get(repo_url, (None, None))
        is_sha = SHA_PATTERN.fullmatch(rev) is not None
        bumps.append(
            Bump(
                name,
                old_comment or old_rev,
                (comment or rev) if is_sha else rev,
                "pre-commit",
                sha=rev if is_sha else None,
            )
        )
    return bumps


def requires_python_floor(pyproject_text: str | None) -> Version:
    spec = tomllib.loads(pyproject_text or "").get("project", {}).get("requires-python")
    if not spec:
        raise ValueError("pyproject.toml has no project.requires-python")
    floors = [Version(item.version) for item in SpecifierSet(spec) if item.operator in {">=", "~=", "=="}]
    if not floors:
        raise ValueError(f"requires-python {spec!r} has no lower bound")
    return min(floors)


def cooldown_days(dependabot_yml_text: str | None, ecosystem: str) -> int:
    config = yaml.safe_load(dependabot_yml_text or "") or {}
    for update in config.get("updates", []):
        if update.get("package-ecosystem") == ecosystem:
            return int((update.get("cooldown") or {}).get("default-days", DEFAULT_COOLDOWN_DAYS))
    return DEFAULT_COOLDOWN_DAYS


def _dedupe(bumps: Iterable[Bump]) -> list[Bump]:
    # #745 moved the same setup-uv pin in five workflows; vet it once. Two different SHAs under
    # the same version comment stay separate so the one that does not match its tag still BLOCKs.
    return list(dict.fromkeys(bumps))


# --- release metadata ----------------------------------------------------------------------


def _github_repo(bump: Bump) -> str | None:
    # Pre-commit hooks hosted outside GitHub keep their full URL as the name.
    return None if bump.kind == "pypi" or "://" in bump.name else bump.name


def _tag(bump: Bump) -> str | None:
    """The tag a GitHub bump claims: the `rev:` itself, or the version comment beside a SHA."""
    if bump.sha and bump.sha.startswith(bump.new):  # no version comment beside the SHA
        return None
    return bump.new


def _resolve_tag(fetch_json: FetchJson, repo: str, tag: str) -> tuple[str, datetime | None] | None:
    """Return the commit a tag points at and, for annotated tags, the tagger date."""
    ref = _get_or_none(fetch_json, f"{GITHUB_API}/repos/{repo}/git/ref/tags/{quote(tag)}")
    if ref is None:
        return None
    target = ref["object"]
    tagged: datetime | None = None
    while target["type"] == "tag":
        annotated = fetch_json(f"{GITHUB_API}/repos/{repo}/git/tags/{target['sha']}")
        tagged = tagged or _parse_time(annotated["tagger"]["date"])
        target = annotated["object"]
    return target["sha"], tagged


def _pypi_upload_time(files: list[dict[str, Any]]) -> datetime | None:
    return min((_parse_time(item["upload_time_iso_8601"]) for item in files), default=None)


def release_published_at(fetch_json: FetchJson, bump: Bump) -> datetime | None:
    if bump.kind == "pypi":
        release = fetch_json(PYPI_RELEASE.format(name=bump.name, version=bump.new))
        return _pypi_upload_time(release["urls"])
    repo, tag = _github_repo(bump), _tag(bump)
    if repo is None or tag is None:
        return None
    release = _get_or_none(fetch_json, f"{GITHUB_API}/repos/{repo}/releases/tags/{quote(tag)}")
    if release is not None and release.get("published_at"):
        return _parse_time(release["published_at"])
    resolved = _resolve_tag(fetch_json, repo, tag)
    if resolved is None:
        return None
    sha, tagged = resolved
    if tagged is not None:
        return tagged
    return _parse_time(fetch_json(f"{GITHUB_API}/repos/{repo}/git/commits/{sha}")["committer"]["date"])


def later_releases(fetch_json: FetchJson, bump: Bump) -> list[tuple[Version, datetime]] | None:
    """Stable releases newer than the bump, or None when the source publishes no releases."""
    current = _parse_version(bump.new)
    if current is None or (bump.kind != "pypi" and _tag(bump) is None):
        return None
    found: list[tuple[Version, datetime]] = []
    if bump.kind == "pypi":
        for text, files in fetch_json(PYPI_PROJECT.format(name=bump.name))["releases"].items():
            version, uploaded = _parse_version(text), _pypi_upload_time(files)
            if all(item.get("yanked", False) for item in files) or uploaded is None:
                continue
            if version is not None and not version.is_prerelease and version > current:
                found.append((version, uploaded))
        return sorted(found)
    repo = _github_repo(bump)
    if repo is None:
        return None
    # `/releases/latest` follows floating tags (docs/dependency-pipeline.md check 4), so sort ourselves.
    releases = fetch_json(f"{GITHUB_API}/repos/{repo}/releases?per_page={PAGE_SIZE}")
    if not releases:
        return None
    for release in releases:
        version = _parse_version(release["tag_name"])
        if release.get("draft") or release.get("prerelease") or version is None or version.is_prerelease:
            continue
        if version > current and release.get("published_at"):
            found.append((version, _parse_time(release["published_at"])))
    return sorted(found)


def _release_link(bump: Bump) -> str:
    if bump.kind == "pypi":
        return f"https://pypi.org/project/{bump.name}/{bump.new}/"
    if (repo := _github_repo(bump)) and (tag := _tag(bump)):
        return f"https://github.com/{repo}/releases/tag/{tag}"
    return f"https://github.com/{bump.name}"


# --- checks --------------------------------------------------------------------------------


def _result(check_id: str, bump: Bump | None, verdict: str, detail: str, links: Sequence[str] = ()) -> CheckResult:
    return CheckResult(check_id, bump.name if bump else "-", verdict, detail, list(links))


def check_yanked(fetch_json: FetchJson, bump: Bump) -> list[CheckResult]:
    release = fetch_json(PYPI_RELEASE.format(name=bump.name, version=bump.new))
    files = release["urls"]
    if release["info"].get("yanked") or (files and all(item.get("yanked", False) for item in files)):
        # `yanked_reason` is publisher-written free text and stays out of a report agents read.
        return [_result("yanked", bump, "BLOCK", "PyPI で yank 済み (理由は PyPI で確認)", [_release_link(bump)])]
    return [_result("yanked", bump, "OK", "PyPI で yank されていない", [_release_link(bump)])]


def _osv_links(ids: Iterable[str]) -> list[str]:
    return [f"https://osv.dev/vulnerability/{vuln_id}" for vuln_id in ids]


def _range_hit(version: Version, events: list[dict[str, str]]) -> bool | None:
    """Evaluate one OSV ECOSYSTEM range; None when a bound is not a parseable version.

    Follows the OSV evaluation algorithm: a version that is not before any `limit` (`*` being
    infinite) is outside the range, whatever the introduced / fixed events say.
    """
    parsed: list[tuple[str, Version | None]] = []
    for event in events:
        (kind, bound), *_ = event.items()
        if bound == "*":
            parsed.append((kind, None))
            continue
        limit = Version("0") if bound == "0" else _parse_version(bound)
        if limit is None:
            return None
        parsed.append((kind, limit))
    limits = [limit for kind, limit in parsed if kind == "limit"]
    if limits and not any(limit is None or version < limit for limit in limits):
        return False
    affected = False
    for kind, limit in parsed:
        if limit is None or kind == "limit":
            continue
        if kind == "introduced" and version >= limit:
            affected = True
        elif (kind == "fixed" and version >= limit) or (kind == "last_affected" and version > limit):
            affected = False
    return affected


def _action_hits(vulns: list[dict[str, Any]], bump: Bump) -> tuple[list[str], list[str]]:
    """Match GitHub Actions advisories client side: OSV ignores `version` for this ecosystem."""
    version = _parse_version(bump.new)
    hits: list[str] = []
    unevaluable: list[str] = []
    for vuln in vulns:
        for affected in vuln.get("affected", []):
            package = affected.get("package", {})
            if package.get("ecosystem") != "GitHub Actions" or package.get("name", "").lower() != bump.name.lower():
                continue
            if bump.new.removeprefix("v") in {item.removeprefix("v") for item in affected.get("versions", [])}:
                hits.append(vuln["id"])
                continue
            for item in affected.get("ranges", []):
                hit = (
                    _range_hit(version, item.get("events", [])) if version and item.get("type") == "ECOSYSTEM" else None
                )
                if hit is None:
                    unevaluable.append(vuln["id"])
                elif hit:
                    hits.append(vuln["id"])
    return sorted(set(hits)), sorted(set(unevaluable) - set(hits))


def check_advisory(fetch_json: FetchJson, post_json: PostJson, bump: Bump) -> list[CheckResult]:
    if bump.kind == "pypi":
        payload = {"package": {"name": bump.name, "ecosystem": "PyPI"}, "version": bump.new}
        hits = sorted(vuln["id"] for vuln in post_json(OSV_QUERY, payload).get("vulns", []))
        unevaluable: list[str] = []
    else:
        payload = {"package": {"name": bump.name, "ecosystem": "GitHub Actions"}}
        hits, unevaluable = _action_hits(post_json(OSV_QUERY, payload).get("vulns", []), bump)
    if hits:
        return [_result("advisory", bump, "BLOCK", f"OSV に該当 ({', '.join(hits)})", _osv_links(hits))]
    if unevaluable:
        detail = f"OSV に該当なし (評価不能な range: {', '.join(unevaluable)})"
        return [_result("advisory", bump, "OK", detail, _osv_links(unevaluable))]
    return [_result("advisory", bump, "OK", "OSV に該当なし")]


def check_python_range(fetch_json: FetchJson, bump: Bump, floor: Version) -> list[CheckResult]:
    spec = fetch_json(PYPI_RELEASE.format(name=bump.name, version=bump.new))["info"].get("requires_python")
    if not spec:
        return [_result("python_range", bump, "OK", "requires_python の宣言なし", [_release_link(bump)])]
    try:
        supported = SpecifierSet(spec).contains(floor, prereleases=True)
    except InvalidSpecifier:
        return [
            _result("python_range", bump, "WARN", f"requires_python {spec!r} を解釈できない", [_release_link(bump)])
        ]
    verdict = "OK" if supported else "BLOCK"
    relation = "⊇" if supported else "⊉"
    return [_result("python_range", bump, verdict, f"{spec} {relation} {floor}", [_release_link(bump)])]


def check_tag_sha(fetch_json: FetchJson, bump: Bump) -> list[CheckResult]:
    repo, tag = _github_repo(bump), _tag(bump)
    if repo is None or tag is None:
        return [_result("tag_sha", bump, "WARN", "照合するタグを特定できない (GitHub 以外か版コメント無し)")]
    resolved = _resolve_tag(fetch_json, repo, tag)
    link = f"https://github.com/{repo}/releases/tag/{tag}"
    if resolved is None:
        return [_result("tag_sha", bump, "BLOCK", f"タグ {tag} が存在しない (404)", [link])]
    if resolved[0] != bump.sha:
        return [
            _result("tag_sha", bump, "BLOCK", f"タグ {tag} は {resolved[0]} を指し、pin {bump.sha} と不一致", [link])
        ]
    return [_result("tag_sha", bump, "OK", f"タグ {tag} → {resolved[0]}", [link])]


def check_tag_exists(fetch_json: FetchJson, bump: Bump) -> list[CheckResult]:
    repo = _github_repo(bump)
    if repo is None:
        return [_result("tag_exists", bump, "WARN", "GitHub 以外のリポジトリのためタグを確認できない")]
    link = f"https://github.com/{repo}/releases/tag/{bump.new}"
    if _resolve_tag(fetch_json, repo, bump.new) is None:
        return [_result("tag_exists", bump, "BLOCK", f"タグ {bump.new} が存在しない (404)", [link])]
    return [_result("tag_exists", bump, "OK", f"タグ {bump.new} が存在する", [link])]


def check_cooldown(fetch_json: FetchJson, bump: Bump, created_at: datetime, days: int) -> list[CheckResult]:
    published = release_published_at(fetch_json, bump)
    if published is None:
        return [_result("cooldown", bump, "WARN", "公開時刻を取得できない (タグ無し、または照合先不明)")]
    # Calendar days in UTC, as Dependabot counts them: #745 was proposed 6.45 days after the release.
    elapsed = (created_at.date() - published.date()).days
    detail = f"{published.date()} → {created_at.date()} = {elapsed} 日 ({'≥' if elapsed >= days else '<'} {days})"
    return [_result("cooldown", bump, "OK" if elapsed >= days else "WARN", detail, [_release_link(bump)])]


def check_superseded(fetch_json: FetchJson, bump: Bump) -> list[CheckResult]:
    later = later_releases(fetch_json, bump)
    if later is None:
        return [_result("superseded", bump, "OK", "release が無いため評価不能")]
    published = release_published_at(fetch_json, bump)
    if not later or published is None:
        return [_result("superseded", bump, "OK", "後続 release 無し")]
    window = timedelta(days=SUPERSEDED_WINDOW_DAYS)
    quick = [(version, at) for version, at in later if at - published <= window]
    first_version, first_at = (quick or later)[0]
    gap = (first_at - published).total_seconds() / 86400
    detail = f"{first_version} が {first_at:%Y-%m-%dT%H:%MZ} ({gap:.1f} 日後)"
    if quick:
        return [_result("superseded", bump, "WARN", f"{detail}。{SUPERSEDED_WINDOW_DAYS} 日以内の後続 {len(quick)} 件")]
    return [_result("superseded", bump, "OK", f"{detail}。{SUPERSEDED_WINDOW_DAYS} 日の窓の外")]


def check_major_bump(bump: Bump) -> list[CheckResult]:
    old, new = _parse_version(bump.old), _parse_version(bump.new)
    if old is None or new is None:
        return [_result("major_bump", bump, "OK", "旧版が無いか版として解釈できないため比較なし")]
    if old.major != new.major:
        return [_result("major_bump", bump, "WARN", f"major {old.major} → {new.major}")]
    return [_result("major_bump", bump, "OK", f"major {new.major} のまま")]


def _expected(path: str, ecosystem: str | None) -> bool:
    patterns = EXPECTED_FILES[ecosystem] if ecosystem else [p for group in EXPECTED_FILES.values() for p in group]
    return any(PurePosixPath(path).match(pattern) if "*" in pattern else path == pattern for pattern in patterns)


def check_pr_hygiene(
    pr: dict[str, Any], files: list[str], commits: list[dict[str, Any]], ecosystem: str | None, bumps: list[Bump]
) -> list[CheckResult]:
    problems: list[str] = []
    if not bumps:
        problems.append("bump を検出できなかった (SHA pin 以外の uses: など。変更を手で確認する)")
    if ecosystem is None:
        problems.append("head.ref の接頭辞がどのエコシステムにも一致しない")
    if extra := [path for path in files if not _expected(path, ecosystem)]:
        problems.append(f"期待外のファイル: {', '.join(extra)}")
    humans = [c for c in commits if (c.get("author") or {}).get("login") != DEPENDABOT_AUTHOR]
    if humans:
        problems.append(f"Dependabot 以外の commit {len(humans)} 件")
    if problems:
        return [_result("pr_hygiene", None, "WARN", "; ".join(problems))]
    prefix = next(p for p, eco in ECOSYSTEM_BY_PREFIX.items() if eco == ecosystem)
    detail = f"{prefix}、{', '.join(files)}、commit {len(commits)}"
    return [_result("pr_hygiene", None, "OK", detail, [pr.get("html_url", "")] if pr.get("html_url") else [])]


def check_ci_green(fetch_json: FetchJson, repo: str, head_sha: str) -> list[CheckResult]:
    runs = _paged(fetch_json, f"{GITHUB_API}/repos/{repo}/commits/{head_sha}/check-runs", key="check_runs")
    runs = [run for run in runs if run["name"] != SELF_CHECK_NAME]
    if not runs:
        return [_result("ci_green", None, "WARN", "check run が 0 件")]
    pending = [run for run in runs if run["status"] != "completed" or run.get("conclusion") not in GREEN_CONCLUSIONS]
    if pending:
        states = ", ".join(f"{run['name']}: {run.get('conclusion') or run['status']}" for run in pending)
        return [_result("ci_green", None, "WARN", f"未完了または失敗: {states}")]
    counts = Counter(run["conclusion"] for run in runs)
    return [_result("ci_green", None, "OK", " + ".join(f"{name} ×{n}" for name, n in sorted(counts.items())))]


# --- orchestration -------------------------------------------------------------------------

PARSERS: dict[str, tuple[Callable[[str | None, str | None], list[Bump]], Callable[[str], bool]]] = {
    "uv": (uv_lock_bumps, lambda path: path == "uv.lock"),
    "github-actions": (workflow_bumps, lambda path: _expected(path, "github-actions")),
    "pre-commit": (pre_commit_bumps, lambda path: path == ".pre-commit-config.yaml"),
}


def _collect_bumps(
    fetch_text: FetchText, repo: str, files: list[str], ecosystem: str | None, before_ref: str, after_ref: str
) -> list[Bump]:
    bumps: list[Bump] = []
    for name in [ecosystem] if ecosystem else list(PARSERS):
        parse, owns = PARSERS[name]
        for path in files:
            if owns(path):
                bumps.extend(
                    parse(file_at(fetch_text, repo, path, before_ref), file_at(fetch_text, repo, path, after_ref))
                )
    return _dedupe(bumps)


def vet_pull_request(
    fetch_json: FetchJson, fetch_text: FetchText, post_json: PostJson, repo: str, number: int
) -> PullRequestVerdict:
    fetch_json = _memoize(fetch_json)
    pr = load_pull_request(fetch_json, repo, number)
    if pr["user"]["login"] != DEPENDABOT_AUTHOR:
        raise NotDependabotPullRequestError(
            f"PR #{number} は Dependabot の PR ではない (author: {pr['user']['login']})"
        )
    head_sha = pr["head"]["sha"]
    ecosystem = detect_ecosystem(pr["head"]["ref"])
    files = changed_files(fetch_json, repo, number)
    commits = pull_request_commits(fetch_json, repo, number)
    before_ref = merge_base(fetch_json, repo, pr["base"]["sha"], head_sha)
    bumps = _collect_bumps(fetch_text, repo, files, ecosystem, before_ref, head_sha)
    created_at = _parse_time(pr["created_at"])
    dependabot_yml = file_at(fetch_text, repo, ".github/dependabot.yml", head_sha)
    floor = (
        requires_python_floor(file_at(fetch_text, repo, "pyproject.toml", head_sha))
        if any(bump.kind == "pypi" for bump in bumps)
        else None
    )

    checks: list[CheckResult] = []
    for bump in bumps:
        if bump.kind == "pypi":
            checks += check_yanked(fetch_json, bump)
        if bump.kind in {"pypi", "action"}:
            checks += check_advisory(fetch_json, post_json, bump)
        if bump.kind == "pypi" and floor is not None:
            checks += check_python_range(fetch_json, bump, floor)
        if bump.kind == "action" or (bump.kind == "pre-commit" and bump.sha):
            checks += check_tag_sha(fetch_json, bump)
        if bump.kind == "pre-commit" and not bump.sha:
            checks += check_tag_exists(fetch_json, bump)
        days = cooldown_days(dependabot_yml, ECOSYSTEM_BY_KIND[bump.kind])
        checks += check_cooldown(fetch_json, bump, created_at, days)
        checks += check_superseded(fetch_json, bump)
        checks += check_major_bump(bump)
    checks += check_pr_hygiene(pr, files, commits, ecosystem, bumps)
    checks += check_ci_green(fetch_json, repo, head_sha)
    return PullRequestVerdict(number, ecosystem, head_sha, bumps, checks)


# --- reports -------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def _link(url: str) -> str:
    # Repository and tag names in URLs come from the PR; percent-encode anything that could
    # close the link target or the table cell.
    return quote(url, safe=":/?#=&%@+,;~")


def verdict_line(verdict: PullRequestVerdict) -> str:
    level = verdict.verdict
    if level == "OK":
        return "判定: OK"
    return f"判定: {level} ({sum(check.verdict == level for check in verdict.checks)} 件)"


def render_pull_request(verdict: PullRequestVerdict) -> str:
    # PR titles and bodies are deliberately absent: agents read this report (AGENTS.md).
    bumps = {bump.name: bump for bump in verdict.bumps}
    lines = [
        f"## PR #{verdict.number} ({verdict.ecosystem or 'unknown'})",
        "",
        f"head: `{verdict.head_sha}`",
        "",
        "| 依存 | 旧 → 新 | 検査 | 結果 | 根拠 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in verdict.checks:
        bump = bumps.get(check.dependency)
        change = _cell(f"{bump.old or '(新規)'} → {bump.new}") if bump else "-"
        links = " ".join(f"[{index}]({_link(link)})" for index, link in enumerate(check.links, start=1))
        evidence = f"{_cell(check.detail)} {links}".strip()
        cells = [_cell(check.dependency), change, _cell(check.check_id), _cell(check.verdict), evidence]
        lines.append(f"| {' | '.join(cells)} |")
    lines += ["", verdict_line(verdict)]
    return "\n".join(lines) + "\n"


def render_markdown(verdicts: Sequence[PullRequestVerdict]) -> str:
    if not verdicts:
        return "# Dependabot PR の事前検証\n\nopen な Dependabot PR はない。\n"
    return "# Dependabot PR の事前検証\n\n" + "\n".join(render_pull_request(verdict) for verdict in verdicts)


def render_json(verdicts: Sequence[PullRequestVerdict]) -> list[dict[str, Any]]:
    return [
        {
            "pr": verdict.number,
            "ecosystem": verdict.ecosystem,
            "head_sha": verdict.head_sha,
            "verdict": verdict.verdict,
            "bumps": [{key: value for key, value in asdict(bump).items() if key != "sha"} for bump in verdict.bumps],
            "checks": [
                {
                    "id": check.check_id,
                    "dependency": check.dependency,
                    "verdict": check.verdict,
                    "detail": check.detail,
                    "links": check.links,
                }
                for check in verdict.checks
            ],
        }
        for verdict in verdicts
    ]


def resolve_repo(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    match = REMOTE_PATTERN.search(remote)
    return match[1] if match else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int, action="append", help="検査する PR 番号 (複数可)")
    target.add_argument("--all-open", action="store_true", help="open な Dependabot PR をすべて検査する")
    parser.add_argument("--repo", help="owner/name (既定: GITHUB_REPOSITORY → git remote origin)")
    parser.add_argument("--report", type=Path, help="Markdown レポートの書き出し先")
    parser.add_argument("--json", type=Path, help="JSON レポートの書き出し先")
    parser.add_argument("--comment", action="store_true", help="各 PR にレポートをコメントする (手元専用)")
    replay = parser.add_mutually_exclusive_group()
    replay.add_argument("--fixture", type=Path, help="録画済み応答でオフライン再生する")
    replay.add_argument("--record", type=Path, help="実際の応答を録画しながら検査する")
    args = parser.parse_args(argv)

    if args.comment and os.environ.get("GITHUB_ACTIONS") == "true":
        # CI runs with a read-only token on Dependabot PRs; posting is a deliberate local act.
        print("--comment は GitHub Actions 内では使えない (手元から明示的に実行する)", file=sys.stderr)
        return 2
    repo = resolve_repo(args.repo)
    if repo is None:
        print("--repo、GITHUB_REPOSITORY、git remote origin のいずれからもリポジトリを特定できない", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN") or None
    if args.fixture:
        fetch_json, fetch_text, post_json = make_fixture_fetchers(args.fixture)
    elif args.record:
        fetch_json, fetch_text, post_json = make_recording_fetchers(token, args.record)
    else:
        fetch_json, fetch_text, post_json = make_fetchers(token)

    try:
        numbers = args.pr or open_dependabot_prs(fetch_json, repo)
        verdicts = [vet_pull_request(fetch_json, fetch_text, post_json, repo, number) for number in numbers]
    except NotDependabotPullRequestError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (requests.RequestException, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"検査自体が失敗した: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    markdown = render_markdown(verdicts)
    print(markdown, end="")
    if args.report:
        args.report.write_text(markdown, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(render_json(verdicts), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.comment:
        post = make_poster(token)
        for verdict in verdicts:
            post(f"{GITHUB_API}/repos/{repo}/issues/{verdict.number}/comments", {"body": render_pull_request(verdict)})
    return 1 if any(verdict.verdict == "BLOCK" for verdict in verdicts) else 0


if __name__ == "__main__":
    sys.exit(main())
