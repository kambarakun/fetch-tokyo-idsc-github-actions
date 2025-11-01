"""
設定管理のユニットテスト
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.managers.config_manager import (
    CollectionConfig,
    ConfigurationManager,
    DataCollectionConfig,
    DataTypeConfig,
    NotificationConfig,
    QualityConfig,
    ScheduleConfig,
    StorageConfig,
    ValidationResult,
)


class TestValidationResult(unittest.TestCase):
    """ValidationResultのテスト"""

    def test_validation_result_creation(self):
        """ValidationResult作成のテスト"""
        result = ValidationResult()
        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(len(result.warnings), 0)

    def test_add_error(self):
        """エラー追加のテスト"""
        result = ValidationResult()
        result.add_error("Test error")

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Test error", result.errors)

    def test_add_warning(self):
        """警告追加のテスト"""
        result = ValidationResult()
        result.add_warning("Test warning")

        self.assertTrue(result.is_valid)  # 警告では無効にならない
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("Test warning", result.warnings)


class TestConfigurationManager(unittest.TestCase):
    """ConfigurationManagerのテスト"""

    def setUp(self):
        self.config_manager = ConfigurationManager()

    def test_get_default_config(self):
        """デフォルト設定取得のテスト"""
        config = self.config_manager._get_default_config()

        self.assertIsInstance(config, DataCollectionConfig)
        self.assertIsInstance(config.schedule, ScheduleConfig)
        self.assertIsInstance(config.storage, StorageConfig)
        self.assertEqual(len(config.data_types), 9)  # 9種類のデータタイプ

    def test_validate_config_valid(self):
        """有効な設定の検証テスト"""
        config = self.config_manager._get_default_config()
        result = self.config_manager.validate_config(config)

        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.errors), 0)

    def test_validate_config_invalid_batch_size(self):
        """無効なバッチサイズの検証テスト"""
        config = self.config_manager._get_default_config()
        config.collection.batch_size = 0

        result = self.config_manager.validate_config(config)

        self.assertFalse(result.is_valid)
        self.assertIn("Batch size must be at least 1", result.errors)

    def test_validate_config_no_data_types(self):
        """データタイプなしの検証テスト"""
        config = self.config_manager._get_default_config()
        config.collection.data_types_to_collect = []

        result = self.config_manager.validate_config(config)

        self.assertFalse(result.is_valid)
        self.assertIn("At least one data type must be specified", result.errors)

    def test_validate_config_invalid_file_size_limits(self):
        """無効なファイルサイズ制限の検証テスト"""
        config = self.config_manager._get_default_config()
        config.quality.file_size_limits = {"csv": (1000, 100)}  # min > max

        result = self.config_manager.validate_config(config)

        self.assertFalse(result.is_valid)
        self.assertIn("Invalid file size limits for csv", result.errors)

    def test_validate_config_warnings(self):
        """警告の検証テスト"""
        config = self.config_manager._get_default_config()
        config.collection.start_year = 1999  # 2000年より前
        config.notifications.max_issues_per_day = 0

        result = self.config_manager.validate_config(config)

        self.assertTrue(result.is_valid)  # 警告のみなので有効
        self.assertEqual(len(result.warnings), 2)

    def test_parse_config(self):
        """設定解析のテスト"""
        config_dict = {
            "schedule": {"cron": "0 0 * * *", "timezone": "UTC", "manual_trigger_enabled": False},
            "collection": {
                "incremental_mode": False,
                "batch_size": 100,
                "start_year": 2020,
                "end_year": 2025,
                "data_types": ["sentinel_weekly_gender"],
            },
            "storage": {"base_directory": "/tmp/data", "auto_commit": False},
            "quality": {"anomaly_detection_enabled": False},
            "notifications": {"github_issues_enabled": False},
            "data_types": [{"name": "test_type", "enabled": True, "fetch_method": "test_method", "epid_code": "501"}],
        }

        config = self.config_manager._parse_config(config_dict)

        self.assertEqual(config.schedule.cron_expression, "0 0 * * *")
        self.assertEqual(config.schedule.timezone, "UTC")
        self.assertFalse(config.schedule.manual_trigger_enabled)
        self.assertEqual(config.collection.batch_size, 100)
        self.assertEqual(config.storage.base_directory, "/tmp/data")
        self.assertEqual(len(config.data_types), 1)
        self.assertEqual(config.data_types[0].name, "test_type")
        self.assertEqual(config.data_types[0].epid_code, "501")

    @patch("builtins.open", mock_open(read_data=""))
    @patch("yaml.safe_load")
    @patch.object(Path, "exists")
    def test_load_config_from_file(self, mock_exists, mock_yaml_load):
        """ファイルから設定読み込みのテスト"""
        mock_exists.return_value = True
        mock_yaml_load.return_value = {"schedule": {"cron": "0 0 * * *"}, "collection": {"batch_size": 50}}

        config = self.config_manager.load_config()

        self.assertIsInstance(config, DataCollectionConfig)
        mock_yaml_load.assert_called_once()

    @patch.object(Path, "exists")
    def test_load_config_file_not_found(self, mock_exists):
        """設定ファイルが存在しない場合のテスト"""
        mock_exists.return_value = False

        config = self.config_manager.load_config()

        # デフォルト設定が返される
        self.assertIsInstance(config, DataCollectionConfig)
        self.assertEqual(config.collection.batch_size, 50)  # デフォルト値

    def test_config_to_dict(self):
        """設定オブジェクトから辞書への変換テスト"""
        config = self.config_manager._get_default_config()
        config_dict = self.config_manager._config_to_dict(config)

        self.assertIn("schedule", config_dict)
        self.assertIn("collection", config_dict)
        self.assertIn("storage", config_dict)
        self.assertIn("quality", config_dict)
        self.assertIn("notifications", config_dict)
        self.assertIn("data_types", config_dict)

        self.assertEqual(config_dict["schedule"]["cron"], config.schedule.cron_expression)
        self.assertEqual(config_dict["collection"]["batch_size"], config.collection.batch_size)

    @patch("builtins.open", mock_open())
    @patch("yaml.dump")
    def test_save_config(self, mock_yaml_dump):
        """設定保存のテスト"""
        config = self.config_manager._get_default_config()

        with tempfile.NamedTemporaryFile(suffix=".yml", delete=False) as f:
            config_path = Path(f.name)

        try:
            self.config_manager.save_config(config, config_path)
            mock_yaml_dump.assert_called_once()
        finally:
            config_path.unlink(missing_ok=True)

    def test_get_enabled_data_types(self):
        """有効なデータタイプ取得のテスト"""
        config = self.config_manager._get_default_config()

        # いくつかのデータタイプを無効化
        config.data_types[0].enabled = False
        config.data_types[1].enabled = False

        self.config_manager.config = config

        enabled_types = self.config_manager.get_enabled_data_types()

        self.assertEqual(len(enabled_types), 7)  # 9 - 2 = 7
        for dt in enabled_types:
            self.assertTrue(dt.enabled)


class TestDataClasses(unittest.TestCase):
    """データクラスのテスト"""

    def test_schedule_config(self):
        """ScheduleConfigのテスト"""
        config = ScheduleConfig(cron_expression="0 10 * * 1", timezone="Asia/Tokyo", manual_trigger_enabled=True)

        self.assertEqual(config.cron_expression, "0 10 * * 1")
        self.assertEqual(config.timezone, "Asia/Tokyo")
        self.assertTrue(config.manual_trigger_enabled)

    def test_storage_config(self):
        """StorageConfigのテスト"""
        config = StorageConfig(base_directory="data/raw", auto_commit=True, keep_shift_jis=True)

        self.assertEqual(config.base_directory, "data/raw")
        self.assertTrue(config.auto_commit)
        self.assertTrue(config.keep_shift_jis)

    def test_collection_config(self):
        """CollectionConfigのテスト"""
        config = CollectionConfig(incremental_mode=True, batch_size=100, start_year=2020, end_year=2025)

        self.assertTrue(config.incremental_mode)
        self.assertEqual(config.batch_size, 100)
        self.assertEqual(config.start_year, 2020)
        self.assertEqual(config.end_year, 2025)

    def test_data_type_config(self):
        """DataTypeConfigのテスト"""
        config = DataTypeConfig(
            name="sentinel_weekly_gender",
            enabled=True,
            fetch_method="fetch_csv_sentinel_weekly_gender",
            epid_code="501",
        )

        self.assertEqual(config.name, "sentinel_weekly_gender")
        self.assertTrue(config.enabled)
        self.assertEqual(config.fetch_method, "fetch_csv_sentinel_weekly_gender")
        self.assertEqual(config.epid_code, "501")


if __name__ == "__main__":
    unittest.main()
