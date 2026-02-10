"""Phase 2 coverage tests for enhanced fetcher branch-heavy paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.fetchers.enhanced_fetcher import DataFetcherConfig, EnhancedEpidemicDataFetcher, FetchResult


def _build_fetcher() -> EnhancedEpidemicDataFetcher:
    """Create a fetcher tuned for deterministic and fast unit tests."""
    return EnhancedEpidemicDataFetcher(DataFetcherConfig(rate_limit_delay=0.0, enable_jitter=False, max_retries=1))


@pytest.mark.asyncio
async def test_fetch_with_retry_async_restores_metadata_params() -> None:
    """fetch_with_retry_async restores data_type/report_type into metadata params."""
    # Arrange
    fetcher = _build_fetcher()

    def fake_fetch(**_params: Any) -> bytes:
        return b"ok"

    # Act
    with patch.object(fetcher.rate_limiter, "wait_if_needed", new=AsyncMock(return_value=None)):
        result = await fetcher.fetch_with_retry_async(
            fake_fetch,
            data_type="sentinel_weekly_gender",
            report_type="1",
            start_year="2025",
            start_sub_period="1",
            end_year="2025",
            end_sub_period="1",
        )

    # Assert
    assert result.success is True
    assert result.metadata is not None
    assert result.metadata.fetch_params is not None
    assert result.metadata.fetch_params.data_type == "sentinel_weekly_gender"
    assert result.metadata.fetch_params.report_type == "1"


def test_fetch_date_range_weekly_rollover_to_next_year() -> None:
    """fetch_date_range handles weekly year rollover when week exceeds max week."""
    # Arrange
    fetcher = _build_fetcher()

    # Act
    with (
        patch.object(fetcher, "_get_weeks_in_year", return_value=1),
        patch.object(fetcher, "fetch_with_retry", return_value=FetchResult(success=True, data=b"x")) as mock_fetch,
        patch("time.sleep") as mock_sleep,
    ):
        results = fetcher.fetch_date_range("sentinel_weekly_gender", (2025, 1), (2026, 1))

    # Assert
    assert len(results) == 2
    assert mock_fetch.call_count == 2
    assert mock_sleep.call_count == 2
    calls = [call.kwargs for call in mock_fetch.call_args_list]
    assert calls[0]["start_year"] == "2025"
    assert calls[0]["start_sub_period"] == "1"
    assert calls[1]["start_year"] == "2026"
    assert calls[1]["start_sub_period"] == "1"


def test_get_missing_data_monthly_with_filters_and_existing_files() -> None:
    """get_missing_data monthly branch applies target_months filter and existing-file exclusion."""
    # Arrange
    fetcher = _build_fetcher()
    existing_files = [Path("sentinel_monthly_age_2024_02.csv")]

    # Act
    missing = fetcher.get_missing_data(
        "sentinel_monthly_age",
        existing_files,
        start_year=2024,
        end_year=2024,
        target_months=[2, 3],
    )

    # Assert
    assert len(missing) == 1
    assert missing[0].start_year == "2024"
    assert missing[0].start_sub_period == "3"


def test_get_missing_data_weekly_filter_skips_non_target_weeks() -> None:
    """get_missing_data weekly branch skips weeks that are not in target_weeks."""
    # Arrange
    fetcher = _build_fetcher()

    # Act
    with patch.object(fetcher, "_get_weeks_in_year", return_value=3):
        missing = fetcher.get_missing_data(
            "sentinel_weekly_gender",
            [],
            start_year=2024,
            end_year=2024,
            target_weeks=[2],
        )

    # Assert
    assert len(missing) == 1
    assert missing[0].start_sub_period == "2"


def test_parse_existing_files_handles_fetch_params_value_error() -> None:
    """_parse_existing_files continues safely when FetchParams construction raises ValueError."""
    # Arrange
    fetcher = _build_fetcher()
    files = [Path("sentinel_weekly_gender_2025_01.csv")]

    # Act
    with patch("src.fetchers.enhanced_fetcher.FetchParams", side_effect=ValueError("invalid params")):
        parsed = fetcher._parse_existing_files(files, "sentinel_weekly_gender")

    # Assert
    assert parsed == []
