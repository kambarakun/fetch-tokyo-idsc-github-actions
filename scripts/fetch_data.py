#!/usr/bin/env python3
"""
東京都感染症発生動向データの自動取得メインスクリプト
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

# プロジェクトのルートディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetchers.enhanced_fetcher import (
    EnhancedEpidemicDataFetcher,
    DataFetcherConfig,
    FetchParams,
    FetchResult
)
from src.managers.config_manager import ConfigurationManager, DataCollectionConfig
from src.managers.storage_manager import StorageManager


# ロギング設定
def setup_logging(log_file: Optional[str] = None, log_level: str = "INFO"):
    """ロギングのセットアップ"""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    handlers = [logging.StreamHandler()]

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding='utf-8'))

    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=log_format,
        handlers=handlers
    )

    return logging.getLogger(__name__)


class DataCollector:
    """データ収集メインクラス"""

    def __init__(self, config: DataCollectionConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.logger = logging.getLogger(__name__)

        # フェッチャー初期化
        fetcher_config = DataFetcherConfig(
            max_retries=3,
            base_delay=1.0,
            timeout=30,
            rate_limit_delay=1.5
        )
        self.fetcher = EnhancedEpidemicDataFetcher(fetcher_config)

        # ストレージマネージャー初期化
        storage_config = {
            'auto_commit': config.storage.auto_commit and not dry_run,
            'commit_message_template': config.storage.commit_message_template,
            'keep_shift_jis': config.storage.keep_shift_jis
        }
        self.storage = StorageManager(
            Path(config.storage.base_directory),
            storage_config
        )

        # 統計情報
        self.stats = {
            'total_files': 0,
            'successful': 0,
            'failed': 0,
            'duplicates': 0,
            'errors': [],
            'start_time': None,
            'end_time': None
        }

    def collect_data(
        self,
        data_types: Optional[List[str]] = None,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None
    ) -> Dict[str, Any]:
        """データ収集のメイン処理"""
        self.stats['start_time'] = datetime.now()
        self.logger.info(f"データ収集開始: {self.stats['start_time']}")

        # パラメータ設定
        data_types = data_types or self.config.collection.data_types_to_collect
        start_year = start_year or self.config.collection.start_year
        end_year = end_year or datetime.now().year

        self.logger.info(f"収集パラメータ: データタイプ={data_types}, 期間={start_year}-{end_year}")

        # データタイプごとに収集
        for data_type in data_types:
            self.logger.info(f"データタイプ '{data_type}' の収集開始")
            self._collect_data_type(data_type, start_year, end_year)

        # 変更をコミット
        if not self.dry_run and self.config.storage.auto_commit:
            self._commit_changes()

        self.stats['end_time'] = datetime.now()
        self.logger.info(f"データ収集完了: {self.stats['end_time']}")

        # 統計情報を出力
        self._print_statistics()

        return self.stats

    def _collect_data_type(
        self,
        data_type: str,
        start_year: int,
        end_year: int
    ):
        """特定のデータタイプを収集"""
        is_monthly = 'monthly' in data_type

        # 既存ファイルの確認(増分収集モードの場合)
        if self.config.collection.incremental_mode:
            existing_files = self.storage.get_existing_files(data_type=data_type)
            missing_params = self.fetcher.get_missing_data(
                data_type, existing_files, start_year, end_year
            )
            self.logger.info(f"欠損データ: {len(missing_params)}件")
        else:
            # 全期間のパラメータ生成
            missing_params = self._generate_all_params(
                data_type, start_year, end_year, is_monthly
            )

        # バッチ処理
        batch_size = self.config.collection.batch_size
        for i in range(0, len(missing_params), batch_size):
            batch = missing_params[i:i + batch_size]
            self._process_batch(batch, data_type, is_monthly)

            # 実行時間チェック(GitHub Actions制限対策)
            if self._check_execution_time():
                self.logger.warning("実行時間制限に近づいています。処理を中断します。")
                break

    def _process_batch(
        self,
        params_batch: List[FetchParams],
        data_type: str,
        is_monthly: bool
    ):
        """バッチ処理"""
        for params in params_batch:
            self.stats['total_files'] += 1

            # データ取得
            fetch_method = self._get_fetch_method(data_type)
            if not fetch_method:
                self.logger.error(f"不明なデータタイプ: {data_type}")
                self.stats['failed'] += 1
                continue

            # パラメータ準備
            fetch_params = {
                'start_year': params.start_year,
                'start_sub_period': params.start_sub_period,
                'end_year': params.end_year,
                'end_sub_period': params.end_sub_period,
                'pref_code': params.pref_code,
                'hc_code': params.hc_code,
                'epid_code': self._get_epid_code(data_type),
                'total_mode': params.total_mode
            }

            # データ取得実行
            result = self.fetcher.fetch_with_retry(fetch_method, **fetch_params)

            if result.success:
                # データ保存
                if not self.dry_run:
                    save_result = self.storage.save_with_metadata(
                        result.data,
                        data_type,
                        int(params.start_year),
                        int(params.start_sub_period),
                        is_monthly,
                        {'fetch_time': result.fetch_time}
                    )

                    if save_result.is_duplicate:
                        self.stats['duplicates'] += 1
                    elif save_result.success:
                        self.stats['successful'] += 1
                    else:
                        self.stats['failed'] += 1
                        self.stats['errors'].append(save_result.error)
                else:
                    self.logger.info(f"[DRY RUN] データ取得成功: {data_type} {params.start_year}-{params.start_sub_period}")
                    self.stats['successful'] += 1
            else:
                self.stats['failed'] += 1
                self.stats['errors'].append(str(result.error))
                self.logger.error(f"データ取得失敗: {result.error}")

            # レート制限
            time.sleep(1)

    def _get_fetch_method(self, data_type: str):
        """データタイプに対応するフェッチメソッドを取得"""
        return self.fetcher.fetch_methods.get(data_type)

    def _get_epid_code(self, data_type: str) -> str:
        """データタイプに応じた感染症コードを取得"""
        # 保健所別・医療圏別は空文字、それ以外は'00'
        if 'health_center' in data_type or 'medical_district' in data_type or 'notifiable' in data_type:
            return ''
        return '00'

    def _generate_all_params(
        self,
        data_type: str,
        start_year: int,
        end_year: int,
        is_monthly: bool
    ) -> List[FetchParams]:
        """全期間のパラメータを生成"""
        params_list = []
        current_date = datetime.now()

        for year in range(start_year, end_year + 1):
            if is_monthly:
                max_period = 12 if year < current_date.year else current_date.month
                for month in range(1, max_period + 1):
                    params = FetchParams(
                        start_year=str(year),
                        start_sub_period=str(month),
                        end_year=str(year),
                        end_sub_period=str(month),
                        data_type=data_type,
                        report_type=self._get_report_type(data_type)
                    )
                    params_list.append(params)
            else:
                max_week = self._get_weeks_in_year(year)
                if year == current_date.year:
                    max_week = min(max_week, current_date.isocalendar()[1])

                for week in range(1, max_week + 1):
                    params = FetchParams(
                        start_year=str(year),
                        start_sub_period=str(week),
                        end_year=str(year),
                        end_sub_period=str(week),
                        data_type=data_type,
                        report_type=self._get_report_type(data_type)
                    )
                    params_list.append(params)

        return params_list

    def _get_report_type(self, data_type: str) -> str:
        """レポートタイプを取得"""
        report_types = {
            'sentinel_weekly_gender': '1',
            'sentinel_weekly_age': '0',
            'sentinel_weekly_health_center': '2',
            'sentinel_weekly_medical_district': '5',
            'sentinel_monthly_gender': '15',
            'sentinel_monthly_age': '10',
            'sentinel_monthly_health_center': '11',
            'sentinel_monthly_medical_district': '12',
            'notifiable_weekly': '20',
        }
        return report_types.get(data_type, '0')

    def _get_weeks_in_year(self, year: int) -> int:
        """年の週数を取得"""
        return date(year, 12, 28).isocalendar()[1]

    def _check_execution_time(self) -> bool:
        """実行時間制限チェック"""
        if not self.stats['start_time']:
            return False

        elapsed = datetime.now() - self.stats['start_time']
        max_hours = self.config.collection.max_execution_time_hours
        return elapsed.total_seconds() > (max_hours * 3600)

    def _commit_changes(self):
        """変更をGitにコミット"""
        try:
            commit_result = self.storage.commit_changes(
                data_type="epidemic_data",
                date_range=f"{datetime.now().strftime('%Y%m%d')}"
            )
            if commit_result.success:
                self.logger.info(f"変更をコミットしました: {commit_result.message}")
            else:
                self.logger.warning(f"コミット失敗: {commit_result.error}")
        except Exception as e:
            self.logger.exception("コミット中にエラー")

    def _print_statistics(self):
        """統計情報を出力"""
        duration = self.stats['end_time'] - self.stats['start_time']

        self.logger.info("=" * 60)
        self.logger.info("データ収集統計:")
        self.logger.info(f"  処理時間: {duration}")
        self.logger.info(f"  総ファイル数: {self.stats['total_files']}")
        self.logger.info(f"  成功: {self.stats['successful']}")
        self.logger.info(f"  失敗: {self.stats['failed']}")
        self.logger.info(f"  重複: {self.stats['duplicates']}")

        if self.stats['errors']:
            self.logger.info(f"  エラー数: {len(self.stats['errors'])}")
            for i, error in enumerate(self.stats['errors'][:5], 1):
                self.logger.info(f"    {i}. {error}")

        self.logger.info("=" * 60)


def main():
    """メイン関数"""
    parser = argparse.ArgumentParser(
        description="東京都感染症発生動向データの自動取得"
    )

    parser.add_argument(
        '--config',
        type=str,
        default='config/config.yml',
        help='設定ファイルのパス'
    )

    parser.add_argument(
        '--data-types',
        type=str,
        help='収集するデータタイプ(カンマ区切り)'
    )

    parser.add_argument(
        '--start-year',
        type=int,
        help='開始年'
    )

    parser.add_argument(
        '--end-year',
        type=int,
        help='終了年'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='テスト実行(データ保存・コミットをスキップ)'
    )

    parser.add_argument(
        '--log-file',
        type=str,
        help='ログファイルのパス'
    )

    parser.add_argument(
        '--log-level',
        type=str,
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='ログレベル'
    )

    args = parser.parse_args()

    # ロギング設定
    logger = setup_logging(args.log_file, args.log_level)

    try:
        # 設定読み込み
        config_manager = ConfigurationManager(Path(args.config))
        config = config_manager.load_config()

        # データタイプの処理
        data_types = None
        if args.data_types:
            data_types = [dt.strip() for dt in args.data_types.split(',')]

        # データ収集実行
        collector = DataCollector(config, args.dry_run)
        stats = collector.collect_data(
            data_types=data_types,
            start_year=args.start_year,
            end_year=args.end_year
        )

        # 結果をJSONファイルに保存
        if not args.dry_run:
            stats_file = Path('data/logs') / f"stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            stats_file.parent.mkdir(parents=True, exist_ok=True)

            # datetimeオブジェクトを文字列に変換
            stats_json = {
                k: v.isoformat() if isinstance(v, datetime) else v
                for k, v in stats.items()
            }

            with open(stats_file, 'w') as f:
                json.dump(stats_json, f, indent=2, ensure_ascii=False)

        # 終了コード
        if stats['failed'] > 0:
            sys.exit(1)
        else:
            sys.exit(0)

    except Exception as e:
        logger.error(f"予期しないエラー: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()