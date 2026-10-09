"""Tests for the HTTP transport shared by the Dependabot scripts (issue #765)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_dependency_pipeline as watchdog
import http_fetch
import vet_dependabot_prs as vet


@pytest.mark.parametrize(
    ("url", "authorized"),
    [
        ("https://api.github.com/repos/owner/name", True),
        ("https://pypi.org/pypi/ruff/json", False),
        ("https://raw.githubusercontent.com/owner/name/sha/uv.lock", False),
        ("https://api.osv.dev/v1/query", False),
        # A lookalike host must not receive the token.
        ("https://api.github.com.example.org/repos/owner/name", False),
    ],
)
def test_token_is_sent_only_to_the_github_api(url: str, authorized: bool) -> None:
    headers = http_fetch.request_headers(url, "secret-token", accept="text/plain", user_agent="agent")

    assert ("Authorization" in headers) is authorized
    assert headers["Accept"] == "text/plain"
    assert headers["User-Agent"] == "agent"


def test_no_token_means_no_authorization_header() -> None:
    headers = http_fetch.request_headers("https://api.github.com/x", None, accept="a", user_agent="u")

    assert "Authorization" not in headers


@pytest.mark.parametrize(
    ("fetch", "accept", "user_agent"),
    [
        (lambda: vet.make_fetchers("t")[0], "application/vnd.github+json", "fetch-tokyo-idsc-dependabot-vetting"),
        (lambda: vet.make_fetchers("t")[1], "application/vnd.github.raw+json", "fetch-tokyo-idsc-dependabot-vetting"),
        (lambda: watchdog.make_fetchers("t")[0], "application/vnd.github+json", "fetch-tokyo-idsc-dependency-watchdog"),
        (lambda: watchdog.make_fetchers("t")[1], "text/plain", "fetch-tokyo-idsc-dependency-watchdog"),
    ],
)
def test_each_script_keeps_its_headers_and_the_thirty_second_timeout(
    monkeypatch: pytest.MonkeyPatch, fetch: Any, accept: str, user_agent: str
) -> None:
    calls: list[dict[str, Any]] = []

    class _Response:
        ok = True
        text = "{}"

        def json(self) -> Any:
            return {}

    def fake_get(url: str, **kwargs: Any) -> _Response:
        calls.append(kwargs)
        return _Response()

    monkeypatch.setattr(requests, "get", fake_get)

    fetch()("https://api.github.com/repos/owner/name")

    assert calls == [
        {"headers": {"Accept": accept, "User-Agent": user_agent, "Authorization": "Bearer t"}, "timeout": 30}
    ]


def test_not_found_keeps_the_response_so_callers_can_tell_404_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both scripts treat 404 as a signal (no tag, no checksum file) by reading `exc.response`."""

    class _NotFound:
        ok = False
        status_code = 404
        reason = "Not Found"
        text = "missing"

    monkeypatch.setattr(requests, "get", lambda url, headers, timeout: _NotFound())

    for fetch in (vet.make_fetchers(None)[0], watchdog.make_fetchers(None)[0]):
        with pytest.raises(requests.HTTPError) as excinfo:
            fetch("https://api.github.com/repos/owner/name")
        assert excinfo.value.response.status_code == 404
        assert vet._is_not_found(excinfo.value)


def test_error_detail_is_one_bounded_line() -> None:
    response = requests.Response()
    response.status_code = 403
    response.reason = "Forbidden"
    response._content = ("line one\nline two  " + "x" * 500).encode()

    detail = http_fetch.error_detail(response)

    assert detail.startswith("line one line two x")
    assert len(detail) == http_fetch.ERROR_DETAIL_CHARS
    assert http_fetch.status_line(response, "https://u") == "403 Forbidden for https://u"
