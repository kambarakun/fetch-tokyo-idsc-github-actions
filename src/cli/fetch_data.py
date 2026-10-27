#!/usr/bin/env python3
"""
東京都感染症発生動向データの自動取得メインスクリプト
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from src.fetchers import periods
from src.fetchers.enhanced_fetcher import DataFetcherConfig, EnhancedEpidemicDataFetcher, FetchParams, FetchResult
from src.fetchers.response_contract import InvalidResponseError, validate_response
from src.managers.config_manager import ConfigurationManager, DataCollectionConfig
from src.managers.storage_manager import StorageManager, data_file_name, find_existing_files


class CollectionMode(StrEnum):
    """データ収集モード

    Attributes:
        INCREMENTAL: 欠損データのみ取得 (既存ファイルをスキップ)
        FULL: 全期間のデータを取得し、既存ファイルも更新チェック
        FORCE: 全データを強制的に再取得 (既存ファイルも上書き)
    """

    INCREMENTAL = "incremental"
    FULL = "full"
    FORCE = "force"

    def __str__(self) -> str:
        return self.value


# ロギング設定
def setup_logging(log_file: str | None = None, log_level: str = "INFO"):
    """ロギングのセットアップ"""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(level=getattr(logging, log_level.upper()), format=log_format, handlers=handlers)

    return logging.getLogger(__name__)


class FetchPlanner:
    """Selects the periods a run requests, without HTTP requests or writes.

    `fetch-data --plan-only` uses it directly and DataCollector inherits it, so a plan and a real run
    share the mode, period and candidate rules. Planning builds no HTTP session and no StorageManager:
    it uses the fetcher's class-level selection helpers and lists existing files read-only.
    """

    # Shown in every plan: what a plan cannot know without contacting the data source
    NOT_VERIFIED = (
        "データ提供元での実在・公開状況 (未公開の週/月は取得後に全ゼロと判定され保存されない)",
        "取得内容と応答契約 (タイトル・対象期間) の検証結果",
        "HTTP/TLS 接続とリトライの成否",
        "full/force で既存ファイルが実際に書き換わるか (同一内容なら書き換えない)",
        "実行時間上限 (collection.max_execution_time_hours) による途中打ち切り",
    )

    def __init__(
        self,
        config: DataCollectionConfig,
        mode: CollectionMode | str | None = None,
        target_weeks: list[int] | None = None,
        target_months: list[int] | None = None,
        # 後方互換性のため残す (非推奨)
        skip_existing: bool | None = None,
        force_update: bool | None = None,
    ):
        self.config = config
        self.target_weeks = target_weeks
        self.target_months = target_months
        self.logger = logging.getLogger(__name__)

        # モードの決定 (優先順位: mode引数 > 旧フラグ > config > デフォルト)
        if mode is not None:
            # modeが文字列の場合はenumに変換
            self.mode = CollectionMode(mode) if isinstance(mode, str) else mode
        elif force_update:
            # 後方互換性: --force-updateはFORCEモードにマップ
            self.logger.warning("--force-update は非推奨です。--mode=force を使用してください。")
            self.mode = CollectionMode.FORCE
        elif skip_existing:
            # 後方互換性: --skip-existingはINCREMENTALモードにマップ
            self.logger.warning("--skip-existing は非推奨です。--mode=incremental を使用してください。")
            self.mode = CollectionMode.INCREMENTAL
        elif skip_existing is False:
            # 後方互換性: --skip-existing が明示的にFalseの場合はFULLモード
            self.mode = CollectionMode.FULL
        else:
            # config.ymlから読み取る (デフォルトはINCREMENTAL)
            config_mode = getattr(config.collection, "mode", None)
            if config_mode:
                self.mode = CollectionMode(config_mode)
            elif hasattr(config.collection, "incremental_mode") and config.collection.incremental_mode:
                # 旧設定との互換性: incremental_mode=TrueならINCREMENTAL
                self.logger.warning("config.collection.incremental_mode は非推奨です。mode を使用してください。")
                self.mode = CollectionMode.INCREMENTAL
            else:
                # デフォルトはINCREMENTAL (既存ファイルをスキップ)
                self.mode = CollectionMode.INCREMENTAL

        self.logger.info(f"データ収集モード: {self.mode.value}")

    def resolve_targets(
        self, data_types: list[str] | None, start_year: int | None, end_year: int | None
    ) -> tuple[list[str], int, int]:
        """Apply the CLI > config > default precedence to the data types and the year range."""
        data_types = data_types or self.config.collection.data_types_to_collect
        start_year = start_year or self.config.collection.start_year
        # Precedence: --end-year > collection.end_year in config > the current JST year
        end_year = end_year or self.config.collection.end_year or periods.now_jst().year
        return data_types, start_year, end_year

    def select_params(self, data_type: str, start_year: int, end_year: int) -> list[FetchParams]:
        """Periods of `data_type` to request, by mode

        - FORCE: 全データを強制的に再取得
        - FULL: 全期間のデータを取得し、既存も更新チェック
        - INCREMENTAL: 欠損データのみ取得 (既存ファイルをスキップ)
        """
        is_monthly = "monthly" in data_type

        if self.mode == CollectionMode.FORCE:
            # FORCEモード: 全データを強制的に再取得
            self.logger.info(f"[{self.mode.value}] {data_type}の全データを強制再取得")
            return self._generate_all_params(data_type, start_year, end_year, is_monthly)

        if self.mode == CollectionMode.FULL:
            # FULLモード: 全期間のデータを取得、既存も更新チェック
            all_params = self._generate_all_params(data_type, start_year, end_year, is_monthly)
            self.logger.info(f"[{self.mode.value}] {data_type}の{len(all_params)}件を取得 (既存も更新チェック)")
            return all_params

        if self.mode == CollectionMode.INCREMENTAL:
            # INCREMENTALモード: 欠損データのみ取得
            existing_files = self._existing_files(data_type)
            missing_params = self._missing_params(data_type, existing_files, start_year, end_year)
            self.logger.info(f"[{self.mode.value}] {data_type}の欠損データ {len(missing_params)}件のみ取得")
            return missing_params

        # 未知のモード (通常は発生しない)
        raise ValueError(f"Unknown collection mode: {self.mode}")

    def _existing_files(self, data_type: str) -> list[Path]:
        return find_existing_files(Path(self.config.storage.base_directory), data_type=data_type)

    def _missing_params(
        self, data_type: str, existing_files: list[Path], start_year: int, end_year: int
    ) -> list[FetchParams]:
        return EnhancedEpidemicDataFetcher.get_missing_data(
            data_type, existing_files, start_year, end_year, self.target_weeks, self.target_months
        )

    def plan(
        self, data_types: list[str] | None = None, start_year: int | None = None, end_year: int | None = None
    ) -> dict[str, Any]:
        """Build the machine-readable plan printed by `fetch-data --plan-only`.

        Raises:
            ValueError: A data type is unknown, or in incremental mode a target week / month is out of
                range. Full and force modes skip such targets without an error, as a real run does.
        """
        data_types, start_year, end_year = self.resolve_targets(data_types, start_year, end_year)
        unknown = [data_type for data_type in data_types if data_type not in EnhancedEpidemicDataFetcher.REPORT_TYPES]
        if unknown:
            # A real run counts every period of these as failed
            raise ValueError(f"不明なデータタイプ: {unknown}")

        raw_dir = Path(self.config.storage.base_directory)
        candidates: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for data_type in data_types:
            period_key = "month" if "monthly" in data_type else "week"
            selected = self.select_params(data_type, start_year, end_year)
            counts[data_type] = len(selected)
            for params in selected:
                year, period = int(params.start_year), int(params.start_sub_period)
                file_name = data_file_name(data_type, year, period)
                candidates.append(
                    {
                        "data_type": data_type,
                        "year": year,
                        period_key: period,
                        "file": file_name,
                        "exists_locally": (raw_dir / file_name).is_file(),
                    }
                )

        return {
            "kind": "fetch-plan",
            "planned_at_jst": periods.now_jst().isoformat(timespec="seconds"),
            "mode": self.mode.value,
            "start_year": start_year,
            "end_year": end_year,
            "target_weeks": self.target_weeks,
            "target_months": self.target_months,
            "raw_dir": str(raw_dir),
            "data_types": data_types,
            "counts": counts,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "not_verified": list(self.NOT_VERIFIED),
        }

    def _generate_all_params(
        self, data_type: str, start_year: int, end_year: int, is_monthly: bool
    ) -> list[FetchParams]:
        """全期間のパラメータを生成"""
        params_list = []
        now = periods.now_jst()

        for year in range(start_year, end_year + 1):
            max_period = periods.max_period_for_year(year, monthly=is_monthly, now=now)
            if is_monthly:
                for month in range(1, max_period + 1):
                    # 対象月が指定されている場合はフィルタリング
                    if self.target_months and month not in self.target_months:
                        continue
                    params = FetchParams(
                        start_year=str(year),
                        start_sub_period=str(month),
                        end_year=str(year),
                        end_sub_period=str(month),
                        data_type=data_type,
                        report_type=self._get_report_type(data_type),
                    )
                    params_list.append(params)
            else:
                for week in range(1, max_period + 1):
                    # 対象週が指定されている場合はフィルタリング
                    if self.target_weeks and week not in self.target_weeks:
                        continue
                    params = FetchParams(
                        start_year=str(year),
                        start_sub_period=str(week),
                        end_year=str(year),
                        end_sub_period=str(week),
                        data_type=data_type,
                        report_type=self._get_report_type(data_type),
                    )
                    params_list.append(params)

        return params_list

    def _get_report_type(self, data_type: str) -> str:
        """レポートタイプを取得 (フェッチャーと同じ対応表)"""
        return EnhancedEpidemicDataFetcher.REPORT_TYPES.get(data_type, "0")


class DataCollector(FetchPlanner):
    """データ収集メインクラス"""

    def __init__(
        self,
        config: DataCollectionConfig,
        mode: CollectionMode | str | None = None,
        dry_run: bool = False,
        save_all_zero: bool = False,
        target_weeks: list[int] | None = None,
        target_months: list[int] | None = None,
        # 後方互換性のため残す (非推奨)
        skip_existing: bool | None = None,
        force_update: bool | None = None,
    ):
        super().__init__(
            config,
            mode=mode,
            target_weeks=target_weeks,
            target_months=target_months,
            skip_existing=skip_existing,
            force_update=force_update,
        )
        self.dry_run = dry_run
        self.save_all_zero = save_all_zero

        # フェッチャー初期化
        fetcher_config = DataFetcherConfig(max_retries=3, base_delay=1.0, timeout=30, rate_limit_delay=1.5)
        self.fetcher = EnhancedEpidemicDataFetcher(fetcher_config)

        # ストレージマネージャー初期化
        self.storage = StorageManager(Path(config.storage.base_directory), {})

        # 統計情報
        self.stats: dict[str, Any] = {
            "total_files": 0,
            "successful": 0,
            "failed": 0,
            "skipped": 0,  # 全て0のデータとしてスキップされたファイル数
            "new_files": 0,  # 新規追加ファイル数
            "updated_files": 0,  # files rewritten because their content changed
            "unchanged_files": 0,  # files left untouched because the same path already had the same content
            "errors": [],
            "start_time": None,
            "end_time": None,
        }

    # A real run reaches the same rules through its own instances: StorageManager.get_existing_files
    # delegates to find_existing_files, and get_missing_data is a classmethod
    def _existing_files(self, data_type: str) -> list[Path]:
        return self.storage.get_existing_files(data_type=data_type)

    def _missing_params(
        self, data_type: str, existing_files: list[Path], start_year: int, end_year: int
    ) -> list[FetchParams]:
        return self.fetcher.get_missing_data(
            data_type, existing_files, start_year, end_year, self.target_weeks, self.target_months
        )

    def collect_data(
        self, data_types: list[str] | None = None, start_year: int | None = None, end_year: int | None = None
    ) -> dict[str, Any]:
        """データ収集のメイン処理"""
        self.stats["start_time"] = datetime.now(UTC)
        self.logger.info(f"データ収集開始: {self.stats['start_time']}")

        # パラメータ設定
        data_types, start_year, end_year = self.resolve_targets(data_types, start_year, end_year)

        self.logger.info(f"収集パラメータ: データタイプ={data_types}, 期間={start_year}-{end_year}")

        # データタイプごとに収集
        for data_type in data_types:
            self.logger.info(f"データタイプ '{data_type}' の収集開始")
            self._collect_data_type(data_type, start_year, end_year)

        self.stats["end_time"] = datetime.now(UTC)
        self.logger.info(f"データ収集完了: {self.stats['end_time']}")

        # 統計情報を出力
        self._print_statistics()

        return self.stats

    def _collect_data_type(self, data_type: str, start_year: int, end_year: int):
        """特定のデータタイプを収集 (対象期間の選択は FetchPlanner.select_params と共通)"""
        is_monthly = "monthly" in data_type
        missing_params = self.select_params(data_type, start_year, end_year)

        # バッチ処理
        batch_size = self.config.collection.batch_size
        for i in range(0, len(missing_params), batch_size):
            batch = missing_params[i : i + batch_size]
            self._process_batch(batch, data_type, is_monthly)

            # 実行時間チェック(GitHub Actions制限対策)
            if self._check_execution_time():
                self.logger.warning("実行時間制限に近づいています。処理を中断します。")
                break

    def _process_batch(self, params_batch: list[FetchParams], data_type: str, is_monthly: bool):
        """バッチ処理"""
        for params in params_batch:
            self.stats["total_files"] += 1

            # データ取得
            fetch_method = self._get_fetch_method(data_type)
            if not fetch_method:
                self.logger.error(f"不明なデータタイプ: {data_type}")
                self.stats["failed"] += 1
                continue

            # パラメータ準備
            fetch_params = {
                "start_year": params.start_year,
                "start_sub_period": params.start_sub_period,
                "end_year": params.end_year,
                "end_sub_period": params.end_sub_period,
                "pref_code": params.pref_code,
                "hc_code": params.hc_code,
                "epid_code": self._get_epid_code(data_type),
                "total_mode": params.total_mode,
                # Popped by fetch_with_retry_async for the source URL and metadata; not passed to fetch_method
                "data_type": data_type,
                "report_type": params.report_type,
            }

            # データ取得実行
            result = self.fetcher.fetch_with_retry(fetch_method, **fetch_params)

            if result.success and (rejection := self._reject_invalid_response(result, data_type, params)):
                # A 200 body that breaks the contract is not saved and counts as failed; the next scheduled run retries
                self.stats["failed"] += 1
                self.stats["errors"].append(rejection)
                self.logger.error(rejection)
            elif result.success and result.data is not None:
                # データ保存
                if not self.dry_run:
                    # 追加メタデータの準備
                    additional_metadata: dict[str, Any] = {"fetch_time": result.fetch_time}
                    if result.source_url:
                        additional_metadata["source_url"] = result.source_url

                    # force_overwrite only records whether this is a FORCE run; identical content is never rewritten
                    save_result = self.storage.save_with_metadata(
                        result.data,
                        data_type,
                        int(params.start_year),
                        int(params.start_sub_period),
                        is_monthly,
                        additional_metadata,
                        force_overwrite=(self.mode == CollectionMode.FORCE),  # recorded only, not used to decide saving
                        save_all_zero=self.save_all_zero,  # 全て0保存フラグを渡す
                    )

                    # Count priority: unchanged > all-zero skipped > success
                    # - unchanged (is_duplicate) and skipped (is_skipped) are successes but are counted separately
                    # - they win regardless of mode, so an identical re-fetch or all-zero data in FORCE mode
                    #   is counted as unchanged_files / skipped instead of updated_files
                    if save_result.is_duplicate:
                        self.stats["unchanged_files"] += 1
                    elif save_result.is_skipped:
                        self.stats["skipped"] += 1
                    elif save_result.success:
                        self.stats["successful"] += 1
                        if save_result.is_new:
                            self.stats["new_files"] += 1
                        else:
                            self.stats["updated_files"] += 1
                    else:
                        self.stats["failed"] += 1
                        self.stats["errors"].append(save_result.error)
                else:
                    self.logger.info(
                        f"[DRY RUN] データ取得成功: {data_type} {params.start_year}-{params.start_sub_period}"
                    )
                    self.stats["successful"] += 1
            else:
                self.stats["failed"] += 1
                self.stats["errors"].append(str(result.error))
                self.logger.error(f"データ取得失敗: {result.error}")

            # レート制限(ジッター付きでサーバー負荷を分散)
            base_delay = 0.5
            jitter = random.uniform(0, 0.3)
            time.sleep(base_delay + jitter)

    def _reject_invalid_response(self, result: FetchResult, data_type: str, params: FetchParams) -> str | None:
        """Return the error message when the body breaks the response contract, else None."""
        data = result.data
        if data is None:
            # Checked before the contract, which accepts any body for a data type it has no title for
            return (
                f"invalid response for {data_type} {params.start_year}-{params.start_sub_period}: "
                "success without a body"
            )
        try:
            validate_response(data, data_type, int(params.start_year), int(params.start_sub_period))
        except InvalidResponseError as e:
            return (
                f"invalid response for {data_type} {params.start_year}-{params.start_sub_period}: {e} "
                f"(first 80 bytes: {data[:80]!r})"
            )
        return None

    def _get_fetch_method(self, data_type: str):
        """データタイプに対応するフェッチメソッドを取得"""
        return self.fetcher.fetch_methods.get(data_type)

    def _get_epid_code(self, data_type: str) -> str:
        """データタイプに応じた感染症コードを取得"""
        # 保健所別・医療圏別は空文字、それ以外は'00'
        if "health_center" in data_type or "medical_district" in data_type or "notifiable" in data_type:
            return ""
        return "00"

    def _get_weeks_in_year(self, year: int) -> int:
        """年の週数を取得"""
        return periods.weeks_in_year(year)

    def _check_execution_time(self) -> bool:
        """実行時間制限チェック"""
        if not self.stats["start_time"]:
            return False

        elapsed = datetime.now(UTC) - self.stats["start_time"]
        max_hours = self.config.collection.max_execution_time_hours
        return elapsed.total_seconds() > (max_hours * 3600)

    def _print_statistics(self):
        """統計情報を出力"""
        duration = self.stats["end_time"] - self.stats["start_time"]

        self.logger.info("=" * 60)
        self.logger.info("データ収集統計:")
        self.logger.info(f"  処理時間: {duration}")
        self.logger.info(f"  総ファイル数: {self.stats['total_files']}")
        self.logger.info(f"  成功: {self.stats['successful']}")
        self.logger.info(f"    - 新規: {self.stats['new_files']}")
        self.logger.info(f"    - 更新: {self.stats['updated_files']}")
        self.logger.info(f"  失敗: {self.stats['failed']}")
        self.logger.info(f"  変更なし (同一内容): {self.stats['unchanged_files']}")
        self.logger.info(f"  スキップ(全て0): {self.stats['skipped']}")

        if self.stats["errors"]:
            self.logger.info(f"  エラー数: {len(self.stats['errors'])}")
            for i, error in enumerate(self.stats["errors"][:5], 1):
                self.logger.info(f"    {i}. {error}")

        self.logger.info("=" * 60)


def save_stats_to_file(stats: dict[str, Any], logger: logging.Logger) -> None:
    """統計情報をJSONファイルに保存

    Args:
        stats: 統計情報の辞書
        logger: ロガーインスタンス
    """
    # 環境変数FETCH_TIMESTAMPを取得、なければ現在時刻を使用
    # これによりGitHub Actionsワークフローと統計ファイル名が一致する
    fetch_timestamp = os.environ.get("FETCH_TIMESTAMP")

    # FETCH_TIMESTAMPの検証(YYYYMMDD_HHMMSS形式)
    if fetch_timestamp:
        # 正規表現でタイムスタンプ形式を検証
        timestamp_pattern = re.compile(r"^\d{8}_\d{6}$")
        if not timestamp_pattern.match(fetch_timestamp):
            logger.warning(
                f"FETCH_TIMESTAMPの形式が不正です(期待: YYYYMMDD_HHMMSS、実際: {fetch_timestamp})。"
                "現在時刻を使用します。"
            )
            fetch_timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        else:
            logger.info(f"環境変数FETCH_TIMESTAMPを使用: {fetch_timestamp}")
    else:
        fetch_timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        logger.info(f"FETCH_TIMESTAMPが未設定のため、現在時刻を使用: {fetch_timestamp}")

    stats_file = Path("data/logs") / f"stats_{fetch_timestamp}.json"
    stats_file.parent.mkdir(parents=True, exist_ok=True)

    # datetimeオブジェクトを文字列に変換
    stats_json = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in stats.items()}

    # エンコーディングを明示的にUTF-8に指定(プラットフォーム依存を回避)
    with stats_file.open("w", encoding="utf-8") as f:
        json.dump(stats_json, f, indent=2, ensure_ascii=False)
    logger.info(f"統計情報を保存しました: {stats_file}")


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(description="東京都感染症発生動向データの自動取得")

    parser.add_argument("--config", type=str, default="config/config.yml", help="設定ファイルのパス")

    parser.add_argument("--data-types", type=str, help="収集するデータタイプ(カンマ区切り)")

    parser.add_argument("--start-year", type=int, help="開始年")

    parser.add_argument("--end-year", type=int, help="終了年")

    parser.add_argument("--target-weeks", type=str, help="対象週を指定(カンマ区切り、例: 1,2,52)")

    parser.add_argument("--target-months", type=str, help="対象月を指定(カンマ区切り、例: 1,2,12)")

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="テスト実行(データ源へ接続して取得し、データ保存のみスキップ)。オフラインで確認するには --plan-only",
    )

    parser.add_argument(
        "--plan-only",
        action="store_true",
        help=(
            "取得候補を JSON で標準出力に表示して終了する。"
            "データ源への接続・ファイルやディレクトリの作成・統計ファイル出力をしない"
        ),
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["incremental", "full", "force"],
        help=(
            "データ収集モード: "
            "incremental(欠損のみ取得/既存スキップ), "
            "full(全期間取得/既存も更新チェック), "
            "force(全データ強制再取得)"
        ),
    )

    # 後方互換性のため残す (非推奨)
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="(非推奨: --mode=incremental を使用) 既存ファイルをスキップし、新規ファイルのみ取得",
    )

    parser.add_argument(
        "--force-update",
        action="store_true",
        help="(非推奨: --mode=force を使用) 既存ファイルも含めてすべて再取得",
    )

    parser.add_argument(
        "--save-all-zero",
        action="store_true",
        help="全て0のデータも保存する(デフォルト: スキップ)。未発表データのテストや特殊な分析用途に使用",
    )

    parser.add_argument("--log-file", type=str, help="ログファイルのパス")

    parser.add_argument(
        "--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="ログレベル"
    )

    args = parser.parse_args()

    # Rejected before logging is set up, so that a plan never creates the log file
    if args.plan_only and args.dry_run:
        parser.error("--plan-only と --dry-run は同時に指定できません (--dry-run はデータ源へ接続します)")
    if args.plan_only and args.log_file:
        parser.error("--plan-only と --log-file は同時に指定できません (計画はファイルを作成しません)")

    # ロギング設定
    logger = setup_logging(args.log_file, args.log_level)

    try:
        # 設定読み込み
        config_manager = ConfigurationManager(Path(args.config))
        config = config_manager.load_config()

        # データタイプの処理
        data_types = None
        if args.data_types:
            data_types = [dt.strip() for dt in args.data_types.split(",")]

        # 対象週の処理
        target_weeks = None
        if args.target_weeks:
            target_weeks = [int(w.strip()) for w in args.target_weeks.split(",")]
            logger.info(f"対象週を指定: {target_weeks}")

        # 対象月の処理
        target_months = None
        if args.target_months:
            target_months = [int(m.strip()) for m in args.target_months.split(",")]
            logger.info(f"対象月を指定: {target_months}")

        # オプションの互換性チェック
        mode_specified = args.mode is not None
        legacy_specified = args.skip_existing or args.force_update

        if mode_specified and legacy_specified:
            logger.error("--mode と旧オプション (--skip-existing, --force-update) は同時に指定できません")
            logger.error("--mode を使用することを推奨します")
            sys.exit(1)

        if args.skip_existing and args.force_update:
            logger.error("--skip-existing と --force-update は同時に指定できません")
            sys.exit(1)

        # --save-all-zeroは特殊用途のため、誤用防止の警告を表示
        # ログ集約システムでの可読性のため、複数の個別ログとして出力
        if args.save_all_zero:
            logger.warning("--save-all-zero が有効です。未発表データ(全て0のデータ)も保存されます。")
            logger.warning("このオプションは通常の運用では不要です。")
            logger.warning("用途: データ収集システムのテスト、未発表データの存在確認など")

        if args.plan_only:
            planner = FetchPlanner(
                config=config,
                mode=args.mode,
                target_weeks=target_weeks,
                target_months=target_months,
                skip_existing=args.skip_existing if args.skip_existing else None,
                force_update=args.force_update if args.force_update else None,
            )
            plan = planner.plan(data_types=data_types, start_year=args.start_year, end_year=args.end_year)
            # Logs go to stderr, so stdout carries only the JSON
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            sys.exit(0)

        # データ収集実行
        collector = DataCollector(
            config=config,
            mode=args.mode,  # 新しいmode引数
            dry_run=args.dry_run,
            save_all_zero=args.save_all_zero,
            target_weeks=target_weeks,
            target_months=target_months,
            # 後方互換性のため残す
            skip_existing=args.skip_existing if args.skip_existing else None,
            force_update=args.force_update if args.force_update else None,
        )
        stats = collector.collect_data(data_types=data_types, start_year=args.start_year, end_year=args.end_year)

        # 結果をJSONファイルに保存
        if not args.dry_run:
            save_stats_to_file(stats, logger)

        # 終了コード
        if stats["failed"] > 0:
            sys.exit(1)
        else:
            sys.exit(0)

    except Exception as e:
        logger.error(f"予期しないエラー: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
