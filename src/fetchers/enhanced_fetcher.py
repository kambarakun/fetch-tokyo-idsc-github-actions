"""
拡張版データフェッチャー(リトライ、レート制限、エラーハンドリング機能付き)
"""

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
from requests.exceptions import HTTPError, RequestException, Timeout

from .base_fetcher import TokyoEpidemicSurveillanceFetcher

# ロガー設定
logger = logging.getLogger(__name__)


@dataclass
class FetchParams:
    """データ取得パラメータ"""

    start_year: str
    start_sub_period: str
    end_year: str
    end_sub_period: str
    data_type: str
    report_type: str
    pref_code: str = "13"
    hc_code: str = "00"
    epid_code: str = "00"
    total_mode: str = "0"


@dataclass
class FileMetadata:
    """ファイルメタデータ"""

    filename: str
    data_type: str
    date_range: str
    timestamp: datetime
    file_size: int
    sha256_hash: str
    encoding: str = "shift_jis"
    fetch_params: Optional[FetchParams] = None


@dataclass
class FetchResult:
    """データ取得結果"""

    success: bool
    data: Optional[bytes] = None
    metadata: Optional[FileMetadata] = None
    error: Optional[Exception] = None
    retry_count: int = 0
    fetch_time: Optional[float] = None


@dataclass
class DataFetcherConfig:
    """フェッチャー設定"""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    timeout: int = 30
    rate_limit_delay: float = 1.0
    enable_jitter: bool = True
    user_agent: str = "TokyoEpidemicDataFetcher/1.0 (GitHub Actions Automation)"


class RetryHandler:
    """リトライハンドラー"""

    def __init__(self, config: DataFetcherConfig):
        self.config = config

    def calculate_delay(self, attempt: int) -> float:
        """指数バックオフによる遅延時間の計算"""
        delay = min(self.config.base_delay * (2**attempt), self.config.max_delay)

        if self.config.enable_jitter:
            # ジッターを追加してサーバー負荷を分散
            delay += random.uniform(0, 0.5)

        return delay

    async def execute_with_retry(self, func: Callable, *args, **kwargs) -> Any:
        """リトライ機能付き実行"""
        last_error = None

        for attempt in range(self.config.max_retries + 1):
            try:
                result = await func(*args, **kwargs)
                return result

            except (Timeout, ConnectionError, HTTPError) as e:
                last_error = e

                if isinstance(e, HTTPError) and e.response.status_code == 429:
                    # レート制限エラーの場合は長めに待つ
                    delay = self.calculate_delay(attempt + 1) * 2
                else:
                    delay = self.calculate_delay(attempt)

                if attempt < self.config.max_retries:
                    logger.warning(f"Attempt {attempt + 1} failed: {e}. " f"Retrying in {delay:.1f} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Max retries exceeded. Last error: {e}")
                    raise

            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                raise

        if last_error:
            raise last_error


class RateLimiter:
    """レート制限管理"""

    def __init__(self, min_delay: float = 1.0):
        self.min_delay = min_delay
        self.last_request_time = 0

    async def wait_if_needed(self):
        """必要に応じて待機"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_delay:
            await asyncio.sleep(self.min_delay - elapsed)
        self.last_request_time = time.time()


class EnhancedEpidemicDataFetcher(TokyoEpidemicSurveillanceFetcher):
    """拡張版感染症データフェッチャー"""

    def __init__(self, config: Optional[DataFetcherConfig] = None):
        super().__init__()
        self.config = config or DataFetcherConfig()
        self.retry_handler = RetryHandler(self.config)
        self.rate_limiter = RateLimiter(self.config.rate_limit_delay)

        # セッション設定のカスタマイズ
        self.session.headers.update({"User-Agent": self.config.user_agent})

        # データタイプとフェッチメソッドのマッピング
        self.fetch_methods = {
            "sentinel_weekly_gender": self.fetch_csv_sentinel_weekly_gender,
            "sentinel_weekly_age": self.fetch_csv_sentinel_weekly_age,
            "sentinel_weekly_health_center": self.fetch_csv_sentinel_weekly_health_center,
            "sentinel_weekly_medical_district": self.fetch_csv_sentinel_weekly_medical_district,
            "sentinel_monthly_gender": self.fetch_csv_sentinel_monthly_gender,
            "sentinel_monthly_age": self.fetch_csv_sentinel_monthly_age,
            "sentinel_monthly_health_center": self.fetch_csv_sentinel_monthly_health_center,
            "sentinel_monthly_medical_district": self.fetch_csv_sentinel_monthly_medical_district,
            "notifiable_weekly": self.fetch_csv_notifiable_weekly,
        }

    async def fetch_with_retry_async(self, fetch_method: Callable, **params) -> FetchResult:
        """非同期リトライ機能付きデータ取得"""
        start_time = time.time()
        retry_count = 0

        # data_typeとreport_typeを分離（メタデータ用）
        data_type = params.pop("data_type", None)
        report_type = params.pop("report_type", None)

        async def _fetch():
            nonlocal retry_count
            await self.rate_limiter.wait_if_needed()
            retry_count += 1
            return fetch_method(**params)

        try:
            data = await self.retry_handler.execute_with_retry(_fetch)

            # メタデータ用のパラメータを復元
            metadata_params = dict(params)
            if data_type:
                metadata_params["data_type"] = data_type
            if report_type:
                metadata_params["report_type"] = report_type

            # メタデータの生成
            metadata = self._create_metadata(data, metadata_params)

            return FetchResult(
                success=True, data=data, metadata=metadata, retry_count=retry_count, fetch_time=time.time() - start_time
            )

        except Exception as e:
            logger.error(f"Failed to fetch data: {e}")
            return FetchResult(success=False, error=e, retry_count=retry_count, fetch_time=time.time() - start_time)

    def fetch_with_retry(self, fetch_method: Callable, **params) -> FetchResult:
        """同期版リトライ機能付きデータ取得"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.fetch_with_retry_async(fetch_method, **params))
        finally:
            loop.close()

    def fetch_date_range(
        self, data_type: str, start_date: tuple[int, int], end_date: tuple[int, int], **kwargs  # (year, week/month)
    ) -> List[FetchResult]:
        """日付範囲での一括取得"""
        results = []
        fetch_method = self.fetch_methods.get(data_type)

        if not fetch_method:
            raise ValueError(f"Unknown data type: {data_type}")

        # 期間内の全てのデータを取得
        current_year = start_date[0]
        current_period = start_date[1]

        while (current_year, current_period) <= end_date:
            params = {
                "start_year": str(current_year),
                "start_sub_period": str(current_period),
                "end_year": str(current_year),
                "end_sub_period": str(current_period),
                "data_type": data_type,
                "report_type": self._get_report_type(data_type),
                **kwargs,
            }

            result = self.fetch_with_retry(fetch_method, **params)
            results.append(result)

            # 次の期間へ
            if "monthly" in data_type:
                current_period += 1
                if current_period > 12:
                    current_period = 1
                    current_year += 1
            else:  # weekly
                max_weeks = self._get_weeks_in_year(current_year)
                current_period += 1
                if current_period > max_weeks:
                    current_period = 1
                    current_year += 1

            # レート制限を考慮
            time.sleep(self.config.rate_limit_delay)

        return results

    def get_missing_data(
        self, data_type: str, existing_files: List[Path], start_year: int = 2000, end_year: Optional[int] = None
    ) -> List[FetchParams]:
        """欠損データの特定"""
        if end_year is None:
            end_year = datetime.now().year

        existing_params = self._parse_existing_files(existing_files, data_type)
        missing_params = []

        for year in range(start_year, end_year + 1):
            if "monthly" in data_type:
                max_period = 12 if year < datetime.now().year else datetime.now().month
                for month in range(1, max_period + 1):
                    params = FetchParams(
                        start_year=str(year),
                        start_sub_period=str(month),
                        end_year=str(year),
                        end_sub_period=str(month),
                        data_type=data_type,
                        report_type=self._get_report_type(data_type),
                    )
                    if not self._is_params_in_existing(params, existing_params):
                        missing_params.append(params)
            else:  # weekly
                max_period = self._get_weeks_in_year(year)
                if year == datetime.now().year:
                    max_period = min(max_period, datetime.now().isocalendar()[1])

                for week in range(1, max_period + 1):
                    params = FetchParams(
                        start_year=str(year),
                        start_sub_period=str(week),
                        end_year=str(year),
                        end_sub_period=str(week),
                        data_type=data_type,
                        report_type=self._get_report_type(data_type),
                    )
                    if not self._is_params_in_existing(params, existing_params):
                        missing_params.append(params)

        return missing_params

    def _create_metadata(self, data: bytes, params: Dict[str, Any]) -> FileMetadata:
        """メタデータの生成"""
        timestamp = datetime.now()
        data_hash = hashlib.sha256(data).hexdigest()

        # データタイプの推定
        data_type = params.get("data_type", "unknown")

        # 日付範囲の文字列化
        date_range = f"{params.get('start_year', '')}{params.get('start_sub_period', '')}"
        if params.get("end_sub_period") != params.get("start_sub_period"):
            date_range += f"-{params.get('end_sub_period', '')}"

        # ファイル名の生成
        filename = f"{data_type}_{date_range}_{timestamp.strftime('%Y%m%d_%H%M%S')}.csv"

        # FetchParamsの作成（必須フィールドがある場合のみ）
        fetch_params = None
        if params and "data_type" in params and "report_type" in params:
            fetch_params = FetchParams(**params)

        return FileMetadata(
            filename=filename,
            data_type=data_type,
            date_range=date_range,
            timestamp=timestamp,
            file_size=len(data),
            sha256_hash=data_hash,
            fetch_params=fetch_params,
        )

    def _get_weeks_in_year(self, year: int) -> int:
        """指定年の週数を取得"""
        return date(year, 12, 28).isocalendar()[1]

    def _get_report_type(self, data_type: str) -> str:
        """データタイプからレポートタイプを取得"""
        report_type_map = {
            "sentinel_weekly_gender": "1",
            "sentinel_weekly_age": "0",
            "sentinel_weekly_health_center": "2",
            "sentinel_weekly_medical_district": "5",
            "sentinel_monthly_gender": "15",
            "sentinel_monthly_age": "10",
            "sentinel_monthly_health_center": "11",
            "sentinel_monthly_medical_district": "12",
            "notifiable_weekly": "20",
        }
        return report_type_map.get(data_type, "0")

    def _parse_existing_files(self, files: List[Path], data_type: str) -> List[FetchParams]:
        """既存ファイルからパラメータを解析"""
        params_list = []

        for file in files:
            if data_type in file.name:
                # ファイル名からパラメータを抽出
                # 例: sentinel_weekly_gender_2025_1_20250101_120000.csv
                parts = file.stem.split("_")
                if len(parts) >= 5:
                    try:
                        year = parts[-4]
                        period = parts[-3]
                        params = FetchParams(
                            start_year=year,
                            start_sub_period=period,
                            end_year=year,
                            end_sub_period=period,
                            data_type=data_type,
                            report_type=self._get_report_type(data_type),
                        )
                        params_list.append(params)
                    except (IndexError, ValueError):
                        continue

        return params_list

    def _is_params_in_existing(self, params: FetchParams, existing_params: List[FetchParams]) -> bool:
        """パラメータが既存リストに含まれるか確認"""
        for existing in existing_params:
            if (
                params.start_year == existing.start_year
                and params.start_sub_period == existing.start_sub_period
                and params.data_type == existing.data_type
            ):
                return True
        return False
