"""
設定管理システム
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class DataTypeConfig:
    """データタイプ設定"""

    name: str
    enabled: bool = True
    fetch_method: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    epid_code: str = "00"  # 感染症コード


@dataclass
class ScheduleConfig:
    """スケジュール設定"""

    cron_expression: str = "0 2 * * 1"  # 毎週月曜日 AM 2:00 (UTC)
    timezone: str = "Asia/Tokyo"
    manual_trigger_enabled: bool = True


@dataclass
class StorageConfig:
    """ストレージ設定"""

    base_directory: str = "data/raw"
    processed_directory: str = "data/processed"
    log_directory: str = "data/logs"
    directory_structure: str = "{year}/{month}/week_{week}"  # or "{year}/{month}"
    auto_commit: bool = True
    commit_message_template: str = "データ更新: {data_type} - {date_range}"
    keep_shift_jis: bool = True  # Shift_JISエンコーディングを維持


@dataclass
class QualityConfig:
    """品質管理設定"""

    file_size_limits: dict[str, tuple] = field(default_factory=lambda: {"csv": (100, 10485760)})  # 100B - 10MB
    anomaly_detection_enabled: bool = True
    anomaly_threshold: float = 0.3
    quarantine_enabled: bool = True
    quarantine_directory: str = "data/quarantine"


@dataclass
class NotificationConfig:
    """通知設定"""

    github_issues_enabled: bool = True
    create_issue_on_error: bool = True
    create_issue_on_anomaly: bool = True
    issue_labels: list[str] = field(default_factory=lambda: ["data-collection", "automated"])
    max_issues_per_day: int = 10


@dataclass
class CollectionConfig:
    """データ収集設定"""

    incremental_mode: bool = True  # 増分収集モード
    batch_size: int = 50  # 一度に処理するファイル数
    start_year: int = 2000
    end_year: int | None = None  # Noneの場合は現在年
    data_types_to_collect: list[str] = field(
        default_factory=lambda: [
            "sentinel_weekly_gender",
            "sentinel_weekly_age",
            "sentinel_weekly_health_center",
            "sentinel_weekly_medical_district",
            "sentinel_monthly_gender",
            "sentinel_monthly_age",
            "sentinel_monthly_health_center",
            "sentinel_monthly_medical_district",
            "notifiable_weekly",
        ]
    )
    retry_failed: bool = True
    max_execution_time_hours: float = 5.5  # GitHub Actions制限対策


@dataclass
class DataCollectionConfig:
    """データ収集統合設定"""

    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    data_types: list[DataTypeConfig] = field(default_factory=list)


class ValidationResult:
    """設定検証結果"""

    def __init__(self):
        self.is_valid = True
        self.errors = []
        self.warnings = []

    def add_error(self, message: str):
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str):
        self.warnings.append(message)


class ConfigurationManager:
    """設定管理クラス"""

    DEFAULT_CONFIG_PATH = Path("config/config.yml")

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self.config: DataCollectionConfig | None = None

    def load_config(self, config_path: Path | None = None) -> DataCollectionConfig:
        """設定ファイルの読み込みと検証"""
        config_path = config_path or self.config_path

        if not config_path.exists():
            logger.warning(f"Config file not found: {config_path}. Using defaults.")
            self.config = self._get_default_config()
            return self.config

        try:
            with open(config_path, encoding="utf-8") as f:
                config_dict = yaml.safe_load(f) or {}

            self.config = self._parse_config(config_dict)

            # 設定の検証
            validation_result = self.validate_config(self.config)
            if not validation_result.is_valid:
                for error in validation_result.errors:
                    logger.error(f"Config validation error: {error}")
                raise ValueError("Configuration validation failed")

            for warning in validation_result.warnings:
                logger.warning(f"Config warning: {warning}")

            return self.config

        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise

    def validate_config(self, config: DataCollectionConfig) -> ValidationResult:
        """設定の妥当性検証"""
        result = ValidationResult()

        # スケジュール検証
        if not config.schedule.cron_expression:
            result.add_error("Cron expression is required")

        # ストレージ検証
        if not config.storage.base_directory:
            result.add_error("Base directory is required")

        # 収集設定検証
        if config.collection.batch_size < 1:
            result.add_error("Batch size must be at least 1")

        if config.collection.start_year < 2000:
            result.add_warning("Start year is before 2000, data may not be available")

        if config.collection.end_year:
            current_year = datetime.now().year
            if config.collection.end_year > current_year:
                result.add_warning(f"End year {config.collection.end_year} is in the future")

        # データタイプ検証
        if not config.collection.data_types_to_collect:
            result.add_error("At least one data type must be specified")

        # 品質設定検証
        for file_type, (min_size, max_size) in config.quality.file_size_limits.items():
            if min_size >= max_size:
                result.add_error(f"Invalid file size limits for {file_type}")

        # 通知設定検証
        if config.notifications.max_issues_per_day < 1:
            result.add_warning("Max issues per day is very low")

        return result

    def _parse_config(self, config_dict: dict[str, Any]) -> DataCollectionConfig:
        """辞書から設定オブジェクトへの変換"""
        config = DataCollectionConfig()

        # スケジュール設定
        if "schedule" in config_dict:
            schedule = config_dict["schedule"]
            config.schedule = ScheduleConfig(
                cron_expression=schedule.get("cron", "0 2 * * 1"),
                timezone=schedule.get("timezone", "Asia/Tokyo"),
                manual_trigger_enabled=schedule.get("manual_trigger_enabled", True),
            )

        # 収集設定
        if "collection" in config_dict:
            collection = config_dict["collection"]
            config.collection = CollectionConfig(
                incremental_mode=collection.get("incremental_mode", True),
                batch_size=collection.get("batch_size", 50),
                start_year=collection.get("start_year", 2000),
                end_year=collection.get("end_year"),
                data_types_to_collect=collection.get(
                    "data_types",
                    [
                        "sentinel_weekly_gender",
                        "sentinel_weekly_age",
                        "sentinel_weekly_health_center",
                        "sentinel_weekly_medical_district",
                        "sentinel_monthly_gender",
                        "sentinel_monthly_age",
                        "sentinel_monthly_health_center",
                        "sentinel_monthly_medical_district",
                        "notifiable_weekly",
                    ],
                ),
                retry_failed=collection.get("retry_failed", True),
                max_execution_time_hours=collection.get("max_execution_time_hours", 5.5),
            )

        # ストレージ設定
        if "storage" in config_dict:
            storage = config_dict["storage"]
            config.storage = StorageConfig(
                base_directory=storage.get("base_directory", "data/raw"),
                processed_directory=storage.get("processed_directory", "data/processed"),
                log_directory=storage.get("log_directory", "data/logs"),
                directory_structure=storage.get("directory_structure", "{year}/{month}/week_{week}"),
                auto_commit=storage.get("auto_commit", True),
                commit_message_template=storage.get(
                    "commit_message_template", "データ更新: {data_type} - {date_range}"
                ),
                keep_shift_jis=storage.get("keep_shift_jis", True),
            )

        # 品質設定
        if "quality" in config_dict:
            quality = config_dict["quality"]
            config.quality = QualityConfig(
                file_size_limits=quality.get("file_size_limits", {"csv": (100, 10485760)}),
                anomaly_detection_enabled=quality.get("anomaly_detection_enabled", True),
                anomaly_threshold=quality.get("anomaly_threshold", 0.3),
                quarantine_enabled=quality.get("quarantine_enabled", True),
                quarantine_directory=quality.get("quarantine_directory", "data/quarantine"),
            )

        # 通知設定
        if "notifications" in config_dict:
            notifications = config_dict["notifications"]
            config.notifications = NotificationConfig(
                github_issues_enabled=notifications.get("github_issues_enabled", True),
                create_issue_on_error=notifications.get("create_issue_on_error", True),
                create_issue_on_anomaly=notifications.get("create_issue_on_anomaly", True),
                issue_labels=notifications.get("issue_labels", ["data-collection", "automated"]),
                max_issues_per_day=notifications.get("max_issues_per_day", 10),
            )

        # データタイプ設定
        if "data_types" in config_dict:
            for dt in config_dict["data_types"]:
                data_type_config = DataTypeConfig(
                    name=dt["name"],
                    enabled=dt.get("enabled", True),
                    fetch_method=dt.get("fetch_method", ""),
                    parameters=dt.get("parameters", {}),
                    epid_code=dt.get("epid_code", "00"),
                )
                config.data_types.append(data_type_config)

        return config

    def _get_default_config(self) -> DataCollectionConfig:
        """デフォルト設定の取得"""
        config = DataCollectionConfig()

        # デフォルトのデータタイプ設定を追加
        default_data_types = [
            DataTypeConfig(name="sentinel_weekly_gender", fetch_method="fetch_csv_sentinel_weekly_gender"),
            DataTypeConfig(name="sentinel_weekly_age", fetch_method="fetch_csv_sentinel_weekly_age"),
            DataTypeConfig(
                name="sentinel_weekly_health_center",
                fetch_method="fetch_csv_sentinel_weekly_health_center",
                epid_code="",
            ),
            DataTypeConfig(
                name="sentinel_weekly_medical_district",
                fetch_method="fetch_csv_sentinel_weekly_medical_district",
                epid_code="",
            ),
            DataTypeConfig(name="sentinel_monthly_gender", fetch_method="fetch_csv_sentinel_monthly_gender"),
            DataTypeConfig(name="sentinel_monthly_age", fetch_method="fetch_csv_sentinel_monthly_age"),
            DataTypeConfig(
                name="sentinel_monthly_health_center",
                fetch_method="fetch_csv_sentinel_monthly_health_center",
                epid_code="",
            ),
            DataTypeConfig(
                name="sentinel_monthly_medical_district",
                fetch_method="fetch_csv_sentinel_monthly_medical_district",
                epid_code="",
            ),
            DataTypeConfig(name="notifiable_weekly", fetch_method="fetch_csv_notifiable_weekly", epid_code=""),
        ]
        config.data_types = default_data_types

        return config

    def save_config(self, config: DataCollectionConfig, path: Path | None = None):
        """設定をファイルに保存"""
        path = path or self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)

        config_dict = self._config_to_dict(config)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True)

        logger.info(f"Configuration saved to {path}")

    def _config_to_dict(self, config: DataCollectionConfig) -> dict[str, Any]:
        """設定オブジェクトを辞書に変換"""
        return {
            "schedule": {
                "cron": config.schedule.cron_expression,
                "timezone": config.schedule.timezone,
                "manual_trigger_enabled": config.schedule.manual_trigger_enabled,
            },
            "collection": {
                "incremental_mode": config.collection.incremental_mode,
                "batch_size": config.collection.batch_size,
                "start_year": config.collection.start_year,
                "end_year": config.collection.end_year,
                "data_types": config.collection.data_types_to_collect,
                "retry_failed": config.collection.retry_failed,
                "max_execution_time_hours": config.collection.max_execution_time_hours,
            },
            "storage": {
                "base_directory": config.storage.base_directory,
                "processed_directory": config.storage.processed_directory,
                "log_directory": config.storage.log_directory,
                "directory_structure": config.storage.directory_structure,
                "auto_commit": config.storage.auto_commit,
                "commit_message_template": config.storage.commit_message_template,
                "keep_shift_jis": config.storage.keep_shift_jis,
            },
            "quality": {
                "file_size_limits": config.quality.file_size_limits,
                "anomaly_detection_enabled": config.quality.anomaly_detection_enabled,
                "anomaly_threshold": config.quality.anomaly_threshold,
                "quarantine_enabled": config.quality.quarantine_enabled,
                "quarantine_directory": config.quality.quarantine_directory,
            },
            "notifications": {
                "github_issues_enabled": config.notifications.github_issues_enabled,
                "create_issue_on_error": config.notifications.create_issue_on_error,
                "create_issue_on_anomaly": config.notifications.create_issue_on_anomaly,
                "issue_labels": config.notifications.issue_labels,
                "max_issues_per_day": config.notifications.max_issues_per_day,
            },
            "data_types": [
                {
                    "name": dt.name,
                    "enabled": dt.enabled,
                    "fetch_method": dt.fetch_method,
                    "parameters": dt.parameters,
                    "epid_code": dt.epid_code,
                }
                for dt in config.data_types
            ],
        }

    def get_enabled_data_types(self) -> list[DataTypeConfig]:
        """有効なデータタイプのみを取得"""
        if not self.config:
            self.load_config()

        return [dt for dt in self.config.data_types if dt.enabled]
