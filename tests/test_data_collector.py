"""
データ収集機能のテスト(skip_existing, force_update オプション)
"""

import csv
import io
import json
import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cli.fetch_data import DataCollector, main
from src.fetchers.enhanced_fetcher import FetchParams, FetchResult, FileMetadata
from src.fetchers.periods import JST
from src.fetchers.response_contract import TITLES
from src.managers.config_manager import CollectionConfig, DataCollectionConfig, StorageConfig
from src.managers.storage_manager import SaveResult


class TestDataCollectorOptions(unittest.TestCase):
    """DataCollectorのオプション機能テスト"""

    def setUp(self):
        """テストセットアップ"""
        # モック設定
        self.mock_config = Mock(spec=DataCollectionConfig)
        self.mock_config.collection = Mock()
        self.mock_config.collection.incremental_mode = False
        self.mock_config.collection.batch_size = 10
        self.mock_config.collection.data_types_to_collect = ["test_data"]
        self.mock_config.collection.max_execution_time_hours = 6
        self.mock_config.collection.end_year = None
        self.mock_config.storage = Mock()
        self.mock_config.storage.base_directory = "data/raw"
        self.mock_config.storage.keep_shift_jis = True

    @patch("src.cli.fetch_data.StorageManager")
    @patch("src.cli.fetch_data.EnhancedEpidemicDataFetcher")
    def test_skip_existing_option(self, mock_fetcher_class, mock_storage_class):
        """skip_existingオプションのテスト"""
        # モックの設定
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher
        mock_fetcher.get_missing_data.return_value = []  # 欠損データなし
        mock_fetcher.fetch_methods = {"test_data": Mock()}

        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        mock_storage.get_existing_files.return_value = ["file1.csv", "file2.csv"]

        # skip_existing=Trueでコレクター作成
        collector = DataCollector(self.mock_config, dry_run=False, skip_existing=True)

        # データ収集実行
        collector._collect_data_type("test_data", 2024, 2024)

        # get_existing_filesとget_missing_dataが呼ばれたことを確認
        mock_storage.get_existing_files.assert_called_once_with(data_type="test_data")
        mock_fetcher.get_missing_data.assert_called_once()

    @patch("src.cli.fetch_data.StorageManager")
    @patch("src.cli.fetch_data.EnhancedEpidemicDataFetcher")
    def test_force_update_option(self, mock_fetcher_class, mock_storage_class):
        """force_updateオプションのテスト"""
        # モックの設定
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher
        mock_fetcher.fetch_methods = {"test_data": Mock()}

        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage

        # force_update=Trueでコレクター作成
        collector = DataCollector(self.mock_config, dry_run=False, force_update=True)

        # _generate_all_paramsをモック
        with patch.object(collector, "_generate_all_params") as mock_generate:
            mock_generate.return_value = []  # パラメータなし
            collector._collect_data_type("test_data", 2024, 2024)

            # _generate_all_paramsが呼ばれたことを確認(全データ取得)
            mock_generate.assert_called_once_with("test_data", 2024, 2024, False)

        # get_existing_filesが呼ばれていないことを確認(スキップしない)
        mock_storage.get_existing_files.assert_not_called()

    @patch("src.cli.fetch_data.StorageManager")
    @patch("src.cli.fetch_data.EnhancedEpidemicDataFetcher")
    def test_force_update_with_save(self, mock_fetcher_class, mock_storage_class):
        """force_updateオプションでの保存処理テスト"""
        # モックの設定
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher

        # フェッチメソッドのモック
        mock_fetch_method = Mock()
        mock_fetcher.fetch_methods = {"test_data": mock_fetch_method}

        # フェッチ結果のモック
        from datetime import datetime

        mock_result = FetchResult(
            success=True,
            data=b"test data",
            fetch_time=1.0,
            metadata=FileMetadata(
                filename="test.csv",
                data_type="test_data",
                date_range="2024-01",
                timestamp=datetime.now(),
                file_size=9,
                sha256_hash="test_hash",
                encoding="shift_jis",
            ),
        )
        mock_fetcher.fetch_with_retry.return_value = mock_result

        # ストレージのモック
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        mock_save_result = SaveResult(success=True, is_duplicate=False)
        mock_storage.save_with_metadata.return_value = mock_save_result

        # force_update=Trueでコレクター作成
        collector = DataCollector(self.mock_config, dry_run=False, force_update=True)

        # テスト用パラメータを生成
        test_params = FetchParams(
            start_year="2024",
            start_sub_period="1",
            end_year="2024",
            end_sub_period="1",
            data_type="test_data",
            report_type="1",
        )

        # _generate_all_paramsをモック
        with patch.object(collector, "_generate_all_params") as mock_generate:
            mock_generate.return_value = [test_params]

            # データ収集実行
            stats = collector.collect_data(data_types=["test_data"], start_year=2024, end_year=2024)

            # save_with_metadataがforce_overwrite=Trueで呼ばれたことを確認
            mock_storage.save_with_metadata.assert_called_once()
            call_args = mock_storage.save_with_metadata.call_args
            self.assertEqual(call_args[1]["force_overwrite"], True)

            # 統計情報の確認
            self.assertEqual(stats["successful"], 1)
            self.assertEqual(stats["failed"], 0)

    def test_conflicting_options(self):
        """skip_existingとforce_updateの同時指定エラーテスト"""
        # メインスクリプトでのオプション競合チェックをテスト
        import contextlib

        from src.cli.fetch_data import main

        # コマンドライン引数をモック
        test_args = [
            "--skip-existing",
            "--force-update",
            "--start-year",
            "2024",
            "--end-year",
            "2024",
        ]

        with (
            patch("sys.argv", ["fetch-data", *test_args]),
            patch("sys.exit") as mock_exit,
            patch("src.cli.fetch_data.setup_logging") as mock_logging,
        ):
            # sys.exitが実際の挙動と同じくSystemExitを発生させる
            mock_exit.side_effect = SystemExit(1)
            mock_logger = Mock()
            mock_logging.return_value = mock_logger

            # ConfigurationManagerをモック
            with patch("src.cli.fetch_data.ConfigurationManager"), contextlib.suppress(SystemExit):
                main()

            # エラーログが出力されたことを確認
            mock_logger.error.assert_called_once_with("--skip-existing と --force-update は同時に指定できません")
            # sys.exit(1)が最初に呼ばれたことを確認
            first_call = mock_exit.call_args_list[0]
            self.assertEqual(first_call[0][0], 1)


class TestDataCollectorIntegration(unittest.TestCase):
    """DataCollectorの統合テスト"""

    @patch("src.cli.fetch_data.StorageManager")
    @patch("src.cli.fetch_data.EnhancedEpidemicDataFetcher")
    @patch("src.cli.fetch_data.ConfigurationManager")
    def test_normal_mode_without_options(self, mock_config_manager_class, mock_fetcher_class, mock_storage_class):
        """オプションなしの通常モード(skip_existing=False優先)"""
        # 設定マネージャーのモック
        mock_config_manager = Mock()
        mock_config_manager_class.return_value = mock_config_manager
        mock_config = Mock(spec=DataCollectionConfig)
        mock_config.collection = Mock()
        mock_config.collection.incremental_mode = True
        mock_config.collection.batch_size = 10
        mock_config.collection.max_execution_time_hours = 6
        mock_config.collection.end_year = None
        mock_config.storage = Mock()
        mock_config.storage.base_directory = "data/raw"
        mock_config.storage.keep_shift_jis = True
        mock_config_manager.load_config.return_value = mock_config

        # フェッチャーのモック
        mock_fetcher = Mock()
        mock_fetcher_class.return_value = mock_fetcher
        mock_fetcher.get_missing_data.return_value = []
        mock_fetcher.fetch_methods = {}

        # ストレージのモック
        mock_storage = Mock()
        mock_storage_class.return_value = mock_storage
        mock_storage.get_existing_files.return_value = []

        # オプションなしでコレクター作成(skip_existing=False)
        collector = DataCollector(mock_config, dry_run=False, skip_existing=False, force_update=False)

        # skip_existing=Falseの場合、全期間取得モードになる
        collector._collect_data_type("test_data", 2024, 2024)

        # skip_existing=Falseの場合、ワークフローパラメータが優先され全期間取得
        # get_existing_filesやget_missing_dataは呼ばれない(全期間取得のため)
        mock_storage.get_existing_files.assert_not_called()
        mock_fetcher.get_missing_data.assert_not_called()


def _csv_body(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(rows)
    return buffer.getvalue().encode("shift_jis")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def test_collect_data_leaves_saved_files_uncommitted(tmp_path, monkeypatch):
    """collect_data は保存したファイルを Git にコミットしない (コミットは workflow の PR 作成ステップが担う)"""
    # Arrange: the code under test runs git without env=, so isolate git via the process environment
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    head_before = _git(repo, "rev-parse", "HEAD").strip()
    monkeypatch.chdir(repo)

    csv_bytes = _csv_body(
        [
            [TITLES["notifiable_weekly"]],
            ["東京都"],
            ["集計期間開始週", "2025年1週"],
            ["集計期間終了週", "2025年1週"],
            [],
            ["疾病名", "報告数"],
            ["結核", "5"],
            ["インフルエンザ", "3"],
        ]
    )
    cfg = DataCollectionConfig(
        collection=CollectionConfig(start_year=2025, batch_size=5, data_types_to_collect=["notifiable_weekly"]),
        storage=StorageConfig(base_directory=str(repo / "data" / "raw")),
    )

    with patch("src.cli.fetch_data.EnhancedEpidemicDataFetcher") as mock_fetcher_class, patch("time.sleep"):
        mock_fetcher = mock_fetcher_class.return_value
        mock_fetcher.fetch_methods = {"notifiable_weekly": Mock()}
        mock_fetcher.get_missing_data.return_value = [
            FetchParams("2025", "1", "2025", "1", "notifiable_weekly", "weekly")
        ]
        mock_fetcher.fetch_with_retry.return_value = FetchResult(success=True, data=csv_bytes, fetch_time=0.1)

        # Act
        stats = DataCollector(cfg, mode="incremental").collect_data()

    # Assert
    assert _git(repo, "rev-parse", "HEAD").strip() == head_before
    status = _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    assert "?? data/raw/notifiable_weekly_2025_01.csv" in status
    assert stats["new_files"] == 1


# ========== 実際の DataCollector + StorageManager で HTTP 境界だけをモックする ==========

FIXTURE_GENDER = Path(__file__).parent / "fixtures" / "raw" / "sentinel_weekly_gender_2025_10.csv"
GENDER_URL = "https://survey.tmiph.metro.tokyo.lg.jp/epidinfo/dlwgender.do"
HTML_BODY = b"<!DOCTYPE html><html><body>Service Unavailable</body></html>"


def _jst_at(utc_text: str) -> datetime:
    return datetime.fromisoformat(utc_text).replace(tzinfo=UTC).astimezone(JST)


def _collector(tmp_path: Path, monkeypatch, *, mode="incremental", dry_run=False, **collection) -> DataCollector:
    monkeypatch.setattr("src.cli.fetch_data.time.sleep", lambda _seconds: None)
    collection.setdefault("data_types_to_collect", ["sentinel_weekly_gender"])
    config = DataCollectionConfig(
        collection=CollectionConfig(**collection), storage=StorageConfig(base_directory=str(tmp_path / "raw"))
    )
    collector = DataCollector(config, mode=mode, dry_run=dry_run)
    collector.fetcher.rate_limiter.min_delay = 0
    return collector


def _server_returns(body: bytes):
    """Patch the HTTP boundary: every POST answers 200 with `body`"""
    return patch("src.fetchers.base_fetcher.requests.Session.post", return_value=Mock(status_code=200, content=body))


def _week(year: int, week: int) -> FetchParams:
    return FetchParams(str(year), str(week), str(year), str(week), "sentinel_weekly_gender", "1")


def _saved_csvs(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "raw").glob("*.csv"))


def test_invalid_200_body_is_counted_as_failed_and_not_saved(tmp_path, monkeypatch):
    """An HTML page served with 200 is a failed fetch, not an all-zero skip, and nothing is saved"""
    collector = _collector(tmp_path, monkeypatch)

    with _server_returns(HTML_BODY):
        collector._process_batch([_week(2025, 10)], "sentinel_weekly_gender", False)

    stats = collector.stats
    assert (stats["failed"], stats["skipped"], stats["successful"]) == (1, 0, 0)
    assert stats["errors"][0].startswith("invalid response for sentinel_weekly_gender 2025-10: ")
    assert f"(first 80 bytes: {HTML_BODY[:80]!r})" in stats["errors"][0]
    assert _saved_csvs(tmp_path) == []


def test_invalid_200_body_does_not_overwrite_in_force_mode(tmp_path, monkeypatch):
    """FORCE does not replace an existing raw file with an invalid body"""
    collector = _collector(tmp_path, monkeypatch, mode="force")
    existing = tmp_path / "raw" / "sentinel_weekly_gender_2025_10.csv"
    existing.write_bytes(FIXTURE_GENDER.read_bytes())

    with _server_returns(HTML_BODY):
        collector._process_batch([_week(2025, 10)], "sentinel_weekly_gender", False)

    assert collector.stats["failed"] == 1
    assert existing.read_bytes() == FIXTURE_GENDER.read_bytes()


def test_invalid_200_body_is_counted_as_failed_in_dry_run(tmp_path, monkeypatch):
    """--dry-run reports an invalid body as failed too"""
    collector = _collector(tmp_path, monkeypatch, dry_run=True)

    with _server_returns(b""):
        collector._process_batch([_week(2025, 10)], "sentinel_weekly_gender", False)

    assert (collector.stats["failed"], collector.stats["successful"]) == (1, 0)


def test_main_exits_1_when_server_returns_html_200(tmp_path, monkeypatch):
    """fetch-data exits 1 (and records failed=1) when the server answers HTML with 200"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FETCH_TIMESTAMP", raising=False)
    monkeypatch.setattr("src.cli.fetch_data.time.sleep", lambda _seconds: None)
    config_file = tmp_path / "config.yml"
    config_file.write_text(f"storage:\n  base_directory: {json.dumps(str(tmp_path / 'raw'))}\n", encoding="utf-8")
    argv = ["fetch-data", "--config", str(config_file), "--mode", "incremental", "--start-year", "2026"]
    argv += ["--end-year", "2026", "--data-types", "sentinel_weekly_gender", "--target-weeks", "36"]
    monkeypatch.setattr(sys, "argv", argv)

    with (
        patch("src.fetchers.periods.now_jst", return_value=_jst_at("2026-09-24T08:00")),
        _server_returns(HTML_BODY) as mock_post,
        pytest.raises(SystemExit) as excinfo,
    ):
        main()

    assert excinfo.value.code == 1
    assert mock_post.call_args.kwargs["data"]["val(startSubPeriod)"] == "36"
    [stats_file] = (tmp_path / "data" / "logs").glob("stats_*.json")
    assert json.loads(stats_file.read_text(encoding="utf-8"))["failed"] == 1
    assert _saved_csvs(tmp_path) == []


def test_header_only_unpublished_body_is_still_skipped(tmp_path, monkeypatch):
    """A published-shaped body without data rows is still an all-zero skip, not a failure"""
    lines = FIXTURE_GENDER.read_bytes().split(b"\r\n")
    header_only = b"\r\n".join(
        lines[: lines.index('"疾病名","男性","女性","男女合計","定点数"'.encode("shift_jis")) + 1]
    )
    collector = _collector(tmp_path, monkeypatch)

    with _server_returns(header_only):
        collector._process_batch([_week(2025, 10)], "sentinel_weekly_gender", False)

    assert (collector.stats["skipped"], collector.stats["failed"]) == (1, 0)
    assert _saved_csvs(tmp_path) == []


def test_source_url_is_recorded_in_raw_metadata(tmp_path, monkeypatch):
    """A newly saved raw file records the endpoint it was fetched from"""
    collector = _collector(tmp_path, monkeypatch)

    with _server_returns(FIXTURE_GENDER.read_bytes()) as mock_post:
        collector._process_batch([_week(2025, 10)], "sentinel_weekly_gender", False)

    metadata_file = tmp_path / "raw" / ".metadata" / "sentinel_weekly_gender_2025_10.json"
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert collector.stats["new_files"] == 1
    assert metadata["_fetch"]["source_url"] == GENDER_URL
    assert metadata["sources"] == [{"title": "Tokyo IDSC", "path": GENDER_URL}]
    assert mock_post.call_args.args[0] == GENDER_URL


def _requested_years(collector: DataCollector, **collect_kwargs) -> set[str]:
    with patch.object(collector, "_process_batch") as mock_process:
        collector.collect_data(**collect_kwargs)
    return {params.start_year for call in mock_process.call_args_list for params in call.args[0]}


def test_collect_data_uses_config_end_year_when_cli_omits_it(tmp_path, monkeypatch):
    """collection.end_year bounds the enumeration when --end-year is omitted"""
    collector = _collector(tmp_path, monkeypatch, mode="force", start_year=2023, end_year=2024)

    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2026-09-24T08:00")):
        years = _requested_years(collector)

    assert years == {"2023", "2024"}


def test_collect_data_cli_end_year_overrides_config_end_year(tmp_path, monkeypatch):
    """--end-year wins over collection.end_year"""
    collector = _collector(tmp_path, monkeypatch, mode="force", start_year=2023, end_year=2024)

    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2026-09-24T08:00")):
        years = _requested_years(collector, end_year=2023)

    assert years == {"2023"}


def test_collect_data_defaults_end_year_to_jst_year(tmp_path, monkeypatch):
    """Without --end-year or collection.end_year the JST year is used (2027 at 2026-12-31T16:00Z)"""
    collector = _collector(
        tmp_path, monkeypatch, mode="force", start_year=2027, data_types_to_collect=["sentinel_monthly_gender"]
    )

    with patch("src.fetchers.periods.now_jst", return_value=_jst_at("2026-12-31T16:00")):
        years = _requested_years(collector)

    assert years == {"2027"}


def test_check_execution_time_boundaries(tmp_path, monkeypatch):
    """The time limit trips only once elapsed exceeds max_execution_time_hours"""
    collector = _collector(tmp_path, monkeypatch, max_execution_time_hours=1)
    assert collector._check_execution_time() is False

    collector.stats["start_time"] = datetime.now(UTC) - timedelta(seconds=3600 + 60)
    assert collector._check_execution_time() is True

    collector.stats["start_time"] = datetime.now(UTC) - timedelta(seconds=60)
    assert collector._check_execution_time() is False


def test_collect_data_type_processes_every_batch_under_time_limit(tmp_path, monkeypatch):
    """25 periods with batch_size=10 run as 3 batches while under the time limit"""
    collector = _collector(tmp_path, monkeypatch, mode="force", batch_size=10)
    collector.stats["start_time"] = datetime.now(UTC)

    with (
        patch.object(collector, "_generate_all_params", return_value=[_week(2025, w) for w in range(1, 26)]),
        patch.object(collector, "_process_batch") as mock_process,
    ):
        collector._collect_data_type("sentinel_weekly_gender", 2025, 2025)

    assert [len(call.args[0]) for call in mock_process.call_args_list] == [10, 10, 5]


if __name__ == "__main__":
    unittest.main()
