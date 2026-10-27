"""ストレージ管理システム

東京都感染症データのファイル保存、メタデータ管理を担当するモジュール。
フラットなディレクトリ構造でデータファイルを管理する。
A save is skipped when the same path already holds byte-identical content.
"""

import csv
import hashlib
import io
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.models.metadata import METADATA_VERSION
from src.validators.quality_validator import QualityValidator

# 旧バージョンとの互換性のためのエイリアス
LEGACY_METADATA_VERSION = "1.0"

# 検証メッセージの制限
MAX_ERROR_COUNT = 10
MAX_WARNING_COUNT = 10
MAX_MESSAGE_LENGTH = 500

# 検証メッセージのフォーマット (v1.3.0: 統一形式)
CSV_FORMAT_INCONSISTENT_COLUMN_COUNT_MSG = "[csv_format] Inconsistent column count"

# 検証設定
VALIDATION_MIN_FILE_SIZE = 100  # 最小ファイルサイズ(バイト)
VALIDATION_MAX_FILE_SIZE_MB = 50  # 最大ファイルサイズ(MB)
VALIDATION_SIZE_WARNING_THRESHOLD = 0.8  # ファイルサイズ警告閾値 (最大サイズの80%)
VALIDATION_MIN_LINE_COUNT = 1  # 最小行数
VALIDATION_MAX_LINE_COUNT = 1000000  # 最大行数
VALIDATION_MIN_COLUMN_COUNT = 2  # 最小カラム数
VALIDATION_MAX_COLUMN_COUNT = 100  # 最大カラム数
EXPECTED_ENCODING = "shift_jis"  # raw dataのデフォルトエンコーディング (Tokyo IDSCからの取得データ)

logger = logging.getLogger(__name__)


def data_file_name(data_type: str, year: int, period: int) -> str:
    """Raw file name of one period (`data_type` already says weekly or monthly)."""
    return f"{data_type}_{year}_{period:02d}.csv"


def find_existing_files(base_path: Path, data_type: str | None = None, year: int | None = None) -> list[Path]:
    """List the raw CSV files under `base_path` without creating anything (a missing directory lists empty).

    Args:
        base_path: Flat raw data directory.
        data_type: Keep only files whose name contains it (optional).
        year: Keep only files of this year (optional).

    Returns:
        Matching file paths, sorted.
    """
    files = list(base_path.glob("*.csv"))  # flat layout: glob, not rglob

    if data_type:
        files = [f for f in files if data_type in f.name]

    if year:
        # Match the year strictly, e.g. sentinel_weekly_2025_01.csv
        year_pattern = re.compile(rf"_{year}_\d{{2}}\.csv$")
        files = [f for f in files if year_pattern.search(f.name)]

    return sorted(files)


@dataclass
class SaveResult:
    """ファイル保存操作の結果を表すデータクラス。

    Attributes:
        success: 保存操作が成功したかどうか
        file_path: Path of the data file (on success only; also set when unchanged)
        metadata_path: Path of the metadata file (on success only; also set when unchanged)
        error: エラーメッセージ(失敗時のみ)
        is_duplicate: True when unchanged: the same path already held a CSV with the same sha256,
            so the CSV was not rewritten. Also True when only the metadata was rebuilt
        is_new: 新規ファイルとして保存されたかどうか
        is_skipped: 全て0のデータとしてスキップされたかどうか
    """

    success: bool
    file_path: Path | None = None
    metadata_path: Path | None = None
    error: str | None = None
    is_duplicate: bool = False
    is_new: bool = False
    is_skipped: bool = False


class StorageManager:
    """データファイルとメタデータのストレージを管理するクラス。

    Saves Tokyo infectious-disease data files, detects re-saves of identical content
    and manages their metadata.

    Attributes:
        base_path: データ保存のベースディレクトリ
        config: ストレージ設定を含む辞書
        metadata_dir: メタデータファイルを保存するディレクトリ
    """

    def __init__(self, base_path: Path, config: dict[str, Any]):
        """StorageManagerを初期化する。

        Args:
            base_path: データ保存のベースディレクトリ
            config: ストレージ設定を含む辞書
                - ストレージ関連設定 (keep_shift_jis など)
        """
        self.base_path = Path(base_path)
        self.config = config

        # ディレクトリ作成
        self.base_path.mkdir(parents=True, exist_ok=True)

        # メタデータ保存用ディレクトリ
        self.metadata_dir = self.base_path / ".metadata"
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

        # 品質検証器を初期化
        self.quality_validator = QualityValidator(self.base_path)

    def organize_file_path(self, data_type: str, year: int, period: int, is_monthly: bool = False) -> Path:
        """フラットなディレクトリ構造でのファイルパス生成する。

        Args:
            data_type: データタイプ(例: 'sentinel_weekly_age')
            year: 年(例: 2025)
            period: 期間(週番号または月番号)
            is_monthly: 月次データの場合True、週次データの場合False

        Returns:
            ファイルを保存するディレクトリパス(常にbase_path)

        Note:
            現在の実装ではフラット構造のため、すべてのファイルが
            base_path直下に配置される
        """
        # すべてのファイルをrawディレクトリ直下に配置
        dir_path = self.base_path
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def save_with_metadata(
        self,
        data: bytes,
        data_type: str,
        year: int,
        period: int,
        is_monthly: bool = False,
        additional_metadata: dict[str, Any] | None = None,
        force_overwrite: bool = False,
        save_all_zero: bool = False,
    ) -> SaveResult:
        """データファイルとメタデータを保存する。

        Args:
            data: 保存するデータ(バイト形式)
            data_type: データタイプ(例: 'sentinel_weekly_age')
            year: 年(例: 2025)
            period: 期間(週番号または月番号)
            is_monthly: 月次データの場合True、週次データの場合False
            additional_metadata: 追加のメタデータ(オプション)
            force_overwrite: Whether this is a FORCE run. Only recorded in the metadata's
                _fetch.force_overwrite, never used to decide saving (FULL and FORCE behave the same here)
            save_all_zero: 全て0のデータも保存する場合True(デフォルト: False)

        Returns:
            保存操作の結果を含むSaveResultオブジェクト

        Note:
            - The data's SHA256 is compared with the current CSV at the same path. When they match
              and the metadata is current (hash.value matches, metadata_version is the current one),
              neither the CSV nor the metadata is rewritten and the result is unchanged (is_duplicate=True)
            - When the CSV matches but the metadata is missing, broken, mismatched or outdated,
              only the metadata is rebuilt; the result is still unchanged
            - Identical content at another path (another period) is saved, and so is a revert (A→B→A)
            - When an existing file's content is replaced, created is kept from the existing metadata
              in every mode
            - verification/quality are not recomputed for unchanged files; changes to the validation
              logic reach them through verify-metadata / migrate-metadata or a data rebuild
            - 全て0のデータは保存をスキップする(save_all_zeroがFalseの場合)
            - パス安全性チェックは保存前に実行され、失敗時は保存を中断
              (パストラバーサル攻撃等のセキュリティリスクを事前に防止)
            - メタデータは.metadataディレクトリに別途保存される
            - 保存後にデータ品質検証を実行し、結果はmetadata["verification"]に記録
            - データ品質検証失敗(encoding, csv_format, file_size)でもファイルは保存される
              (データ品質検証は記録目的であり、保存の可否は判定しない)
        """
        # data_typeのバリデーション(セキュリティ対策)
        if not self._validate_data_type(data_type):
            error_msg = f"Invalid data_type: {data_type}. Contains invalid characters."
            logger.error(error_msg)
            return SaveResult(success=False, error=error_msg)

        try:
            # データハッシュ計算
            data_hash = hashlib.sha256(data).hexdigest()

            # 全て0のデータかチェック(save_all_zeroがFalseの場合のみ)
            # Runs before the existing file is read, so all-zero data never overwrites a non-zero file
            if not save_all_zero and self._is_all_zero_data(data):
                logger.info(
                    f"Skipping all-zero unpublished data: {data_type}_{year}_{period:02d} "
                    "(use --save-all-zero to save)"
                )
                return SaveResult(success=True, is_skipped=True)

            # ファイルパス生成
            dir_path = self.organize_file_path(data_type, year, period, is_monthly)

            # ファイル名生成(タイムスタンプなし、ゼロパディングあり)
            # データタイプ名に既にweekly/monthlyが含まれているため、period_typeは不要
            filename = data_file_name(data_type, year, period)
            file_path = dir_path / filename

            # パス安全性チェック(保存前に実行 - セキュリティクリティカル)
            # パストラバーサル攻撃等が検出された場合は保存を中断
            path_safety_error = self._check_path_safety_pre_save(file_path)
            if path_safety_error:
                return SaveResult(success=False, error=path_safety_error)

            metadata_filename = f"{filename.replace('.csv', '.json')}"
            metadata_path = self.metadata_dir / metadata_filename

            # 新規ファイルかどうかを判定
            is_new_file = not file_path.exists()

            # Compare with the current content at the same path; read the existing metadata in every mode to keep created
            csv_unchanged = False
            existing_metadata = None
            if not is_new_file:
                csv_unchanged = hashlib.sha256(file_path.read_bytes()).hexdigest() == data_hash
                existing_metadata = self.get_metadata(file_path)

            if csv_unchanged and self._is_metadata_current(existing_metadata, data_hash):
                logger.info(f"Unchanged file (same content): {file_path}")
                return SaveResult(success=True, file_path=file_path, metadata_path=metadata_path, is_duplicate=True)

            if not csv_unchanged:
                # CSVファイル保存(Shift_JISのまま) - 原子的書き込みで安全性を確保
                # 一時ファイルを作成して書き込み
                temp_fd, temp_path = tempfile.mkstemp(dir=file_path.parent, prefix=f".{file_path.stem}_", suffix=".tmp")
                try:
                    os.write(temp_fd, data)
                    os.close(temp_fd)

                    # 原子的にファイルを置き換え(POSIX準拠)
                    # これにより、書き込み中のエラーでもデータロスを防げる
                    Path(temp_path).replace(file_path)
                except Exception:
                    # エラー時は一時ファイルをクリーンアップ
                    temp_file = Path(temp_path)
                    if temp_file.exists():
                        temp_file.unlink()
                    raise

            # メタデータ生成
            period_type = "monthly" if is_monthly else "weekly"
            now = datetime.now(UTC).isoformat()

            # created_at/updated_atの設定
            created_at, updated_at = self._determine_timestamps(existing_metadata, now)

            # 物理行数のカウント
            line_count = self._count_lines(data)

            # additional_metadataからフェッチ関連情報を抽出
            source_url = None
            fetch_time = 0.0
            if additional_metadata:
                source_url = additional_metadata.get("source_url")
                fetch_time = additional_metadata.get("fetch_time", 0.0)

            # メタデータ構築 (v1.1形式)
            metadata = self._build_metadata(
                filename=filename,
                data_type=data_type,
                year=year,
                period=period,
                period_type=period_type,
                created_at=created_at,
                updated_at=updated_at,
                file_size=len(data),
                line_count=line_count,
                data_hash=data_hash,
                file_path=file_path,
                force_overwrite=force_overwrite,
                save_all_zero=save_all_zero,
                source_url=source_url,
                fetch_time=fetch_time,
            )

            # 検証の実行
            verification = self._validate_saved_file(file_path, data)
            metadata["verification"] = verification

            # 品質検証の実行
            # processing_metadataは空でOK (GenderSumValidatorは使用しない)
            quality = self.quality_validator.validate(filename, data_type, {})
            metadata["quality"] = quality

            # メタデータは別ディレクトリに保存(.metadataディレクトリ)
            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            if csv_unchanged:
                logger.info(f"Rebuilt metadata for unchanged file: {file_path}")
            else:
                logger.info(f"Saved file: {file_path} (new={is_new_file})")

            return SaveResult(
                success=True,
                file_path=file_path,
                metadata_path=metadata_path,
                is_new=is_new_file,
                is_duplicate=csv_unchanged,
            )

        except Exception as e:
            logger.exception("Failed to save file")
            return SaveResult(success=False, error=str(e))

    def _is_metadata_current(self, metadata: dict[str, Any] | None, data_hash: str) -> bool:
        """Return whether the existing metadata can be kept as is for an unchanged file.

        Args:
            metadata: Existing metadata read by get_metadata() (None when absent or unreadable)
            data_hash: SHA256 of the data being saved

        Returns:
            True when hash.value equals data_hash and metadata_version is the current one
        """
        if metadata is None:
            return False
        hash_info = metadata.get("hash")
        return (
            isinstance(hash_info, dict)
            and hash_info.get("value") == data_hash
            and metadata.get("metadata_version") == METADATA_VERSION
        )

    def _validate_data_type(self, data_type: str) -> bool:
        """data_typeパラメータの妥当性を検証する。

        Args:
            data_type: 検証するデータタイプ文字列

        Returns:
            安全な文字列の場合True、危険な文字を含む場合False

        Note:
            パストラバーサル攻撃や不正な文字を防ぐため、
            英数字とアンダースコアのみを許可する。
        """
        # 英数字とアンダースコアのみを許可
        pattern = re.compile(r"^[a-zA-Z0-9_]+$")
        return bool(pattern.match(data_type))

    def _is_skippable_row(self, row: list[str]) -> bool:
        """CSVの行がスキップすべき行かどうかを判定する。

        Args:
            row: CSV行のセルのリスト

        Returns:
            スキップすべき行の場合True、それ以外False
        """
        # 空行をスキップ
        if not row or all(not cell.strip() for cell in row):
            return True

        # 最初のセルが注釈行(*で始まる)をスキップ
        first_cell = row[0].strip()
        if first_cell.startswith("*"):
            return True

        # 1列以下の行はスキップ(データ行ではない)
        return len(row) <= 1

    def _check_numeric_cells(self, cells: list[str]) -> tuple[bool, bool]:
        """数値セルをチェックして、数値があるか、0以外の値があるかを返す。

        Args:
            cells: チェックするセルのリスト

        Returns:
            (数値セルがあるか, 0以外の値があるか) のタプル
        """
        has_numeric = False
        has_non_zero = False

        for cell in cells:
            cell_value = cell.strip()

            # 空文字列はスキップ
            if not cell_value:
                continue

            # 数字かどうかチェック(整数または浮動小数点数)
            try:
                numeric_value = float(cell_value)
                has_numeric = True

                # 0以外の値があれば記録(0.0や-0.0は0として扱う)
                if numeric_value != 0.0:
                    has_non_zero = True
                    # 早期リターン: 0以外が見つかったら即座に返す
                    return has_numeric, has_non_zero

            except ValueError:
                # 数値でない場合はスキップ(ヘッダー行や疾病名など)
                continue

        return has_numeric, has_non_zero

    def _is_all_zero_data(self, data: bytes) -> bool:
        """データの全ての数値カラムが0かどうかをチェックする。

        Args:
            data: チェックするCSVデータ(Shift_JISエンコーディング)

        Returns:
            全ての数値カラムが0または空の場合True、それ以外False

        Note:
            未発表データは全てのカウントが0になっているため、
            そのようなデータは保存する価値がないとしてスキップする。
            ヘッダー行や注釈行は無視し、データ行のみをチェックする。

            RFC 4180準拠のCSV解析により、フィールド内のカンマや
            引用符のエスケープを正しく処理する。
        """
        try:
            # Shift_JISでデコードしてStringIOオブジェクトを作成
            content = data.decode("shift_jis")
            csv_reader = csv.reader(io.StringIO(content))

            for row in csv_reader:
                # スキップすべき行かチェック
                if self._is_skippable_row(row):
                    continue

                # 最初のカラムは通常、行ラベル(年齢、地域名など)
                # 2列目以降が数値データ
                numeric_columns = row[1:]

                # 数値カラムがあるかチェック
                _, has_non_zero_in_row = self._check_numeric_cells(numeric_columns)

                # 0以外の値があれば即座にFalseを返す(保存対象)
                if has_non_zero_in_row:
                    return False

            # 以下の場合はスキップ対象(True):
            # 1. ヘッダーのみでデータ行がない(未発表データ)
            # 2. データ行はあるが全ての数値が0
            return True

        except UnicodeDecodeError:
            # Shift_JISデコードエラー - 安全側に倒して保存する
            logger.exception(
                "Failed to decode CSV data as Shift_JIS. " "Saving data as-is for safety (fail-safe behavior)."
            )
            return False
        except csv.Error:
            # CSV解析エラー - 安全側に倒して保存する
            logger.exception("Failed to parse CSV data. " "Saving data as-is for safety (fail-safe behavior).")
            return False
        except Exception:
            # その他の予期しないエラー - 安全側に倒して保存する
            logger.exception(
                "Unexpected error while checking for all-zero data. "
                "Saving data as-is for safety (fail-safe behavior)."
            )
            return False

    def _count_lines(self, data: bytes) -> int | None:
        """CSVの物理行数をカウントする。

        Args:
            data: CSVデータ(バイト形式)

        Returns:
            物理行数 (改行文字の数に基づく)。処理失敗時はNone。
            空データの場合は0を返す。

        Note:
            改行文字(\\n)の数をカウントして物理行数を算出する。
            末尾が改行でない場合は1を追加。
            ヘッダー行を含む全ての行をカウントする (CSVのデータ行数ではない)。
        """
        try:
            # 空データの場合は0を返す
            if not data:
                return 0
            count = data.count(b"\n")
            # 末尾が改行でない場合は1を追加
            if not data.endswith(b"\n"):
                count += 1
        except (TypeError, AttributeError) as e:
            logger.warning(f"Failed to count rows: {e}")
            return None
        else:
            return count

    def _determine_timestamps(self, existing_metadata: dict[str, Any] | None, now: str) -> tuple[str, str]:
        """created_atとupdated_atを決定する。

        Args:
            existing_metadata: Existing metadata (read in every mode when the file already exists)
            now: 現在時刻のISO文字列

        Returns:
            (created_at, updated_at) のタプル

        Note:
            v1.0形式(created_at/timestamp)とv1.1形式(created)の両方に対応。
        """
        if existing_metadata:
            # 既存のcreated_at/createdを保持、なければtimestampから復元
            created_at = (
                existing_metadata.get("created_at")
                or existing_metadata.get("created")
                or existing_metadata.get("timestamp")
                or now
            )
        else:
            created_at = now
        return created_at, now

    def _build_metadata(
        self,
        *,
        filename: str,
        data_type: str,
        year: int,
        period: int,
        period_type: str,
        created_at: str,
        updated_at: str,
        file_size: int,
        line_count: int | None,
        data_hash: str,
        file_path: Path,
        force_overwrite: bool,
        save_all_zero: bool,
        source_url: str | None = None,
        fetch_time: float = 0.0,
    ) -> dict[str, Any]:
        """メタデータ辞書を構築する (v1.1形式)。

        Args:
            filename: ファイル名
            data_type: データタイプ
            year: 年
            period: 期間
            period_type: 期間タイプ ("weekly" or "monthly")
            created_at: 作成日時
            updated_at: 更新日時
            file_size: ファイルサイズ
            line_count: 物理行数 (ヘッダー含む)
            data_hash: SHA256ハッシュ
            file_path: ファイルパス
            force_overwrite: 強制上書きフラグ
            save_all_zero: 全て0保存フラグ
            source_url: 取得元URL
            fetch_time: 取得時間 (秒)

        Returns:
            メタデータ辞書 (v1.1形式)
        """
        # 名前を生成 (ファイル名から拡張子を除く)
        name = filename.replace(".csv", "")

        # ISO 8601形式に正規化 (タイムゾーン付き)
        created_iso = self._normalize_timestamp(created_at)
        modified_iso = self._normalize_timestamp(updated_at)

        # ソース情報
        sources = []
        if source_url:
            sources.append({"title": "Tokyo IDSC", "path": source_url})

        return {
            "metadata_version": METADATA_VERSION,
            "name": name,
            "filename": filename,
            "path": str(file_path.relative_to(self.base_path)),
            "profile": "tokyo-idsc-raw",
            "data_type": data_type,
            "temporal": {
                "year": year,
                "period": period,
                "period_type": period_type,
            },
            "bytes": file_size,
            "lines": line_count,
            "hash": {
                "algorithm": "sha256",
                "value": data_hash,
            },
            "encoding": "shift_jis",
            "created": created_iso,
            "modified": modified_iso,
            "sources": sources,
            "_fetch": {
                "source_url": source_url,
                "fetch_time_seconds": fetch_time,
                "force_overwrite": force_overwrite,
                "save_all_zero": save_all_zero,
            },
        }

    def _normalize_timestamp(self, timestamp: str) -> str:
        """タイムスタンプをISO 8601形式に正規化する。

        Args:
            timestamp: 入力タイムスタンプ

        Returns:
            ISO 8601形式のタイムスタンプ (UTC)
        """
        try:
            # 既にISO形式の場合はパースを試みる
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            # タイムゾーンがない場合はローカルタイムとして扱いUTCに変換
            if dt.tzinfo is None:
                dt = dt.astimezone(UTC)
            return dt.isoformat()
        except (ValueError, AttributeError) as e:
            # パース失敗時は警告を出力して現在時刻を返す
            logger.warning(f"Failed to parse timestamp '{timestamp}': {e}. Using current time as fallback.")
            return datetime.now(UTC).isoformat()

    def _validate_saved_file(self, file_path: Path, data: bytes) -> dict[str, Any]:
        """保存されたファイルを検証し、検証結果を返す。

        Args:
            file_path: 検証するファイルのパス
            data: ファイルのデータ(バイト形式)

        Returns:
            検証結果の辞書(verification オブジェクト)
        """
        errors: list[str] = []
        warnings: list[str] = []
        checks: dict[str, bool] = {}
        details: dict[str, Any] = {}

        # ファイルサイズチェック
        size_result = self._check_file_size_validation(data)
        checks["file_size"] = size_result["valid"]
        errors.extend(size_result.get("errors", []))
        warnings.extend(size_result.get("warnings", []))

        # エンコーディングチェック
        encoding_result = self._check_encoding_validation(data)
        checks["encoding"] = encoding_result["valid"]
        errors.extend(encoding_result.get("errors", []))

        # CSVフォーマットチェック
        # エンコーディング検証で取得したデコード済みコンテンツを再利用 (パフォーマンス最適化)
        decoded_content = encoding_result.get("decoded_content")
        csv_result = self._check_csv_format_validation(data, decoded_content)
        checks["csv_format"] = csv_result["valid"]
        errors.extend(csv_result.get("errors", []))
        warnings.extend(csv_result.get("warnings", []))
        if csv_result.get("details"):
            details.update(csv_result["details"])

        # パス安全性チェック
        path_result = self._check_path_safety_validation(file_path)
        checks["path_safety"] = path_result["valid"]
        errors.extend(path_result.get("errors", []))

        # ステータスの決定
        status = "failed" if errors else "verified"

        # メッセージの制限
        errors = self._truncate_messages(errors, MAX_ERROR_COUNT)
        warnings = self._truncate_messages(warnings, MAX_WARNING_COUNT)

        result = {
            "status": status,
            "verified_at": datetime.now(UTC).isoformat(),
            "method": "automated",
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
        }

        # 詳細情報がある場合のみdetailsフィールドを追加
        if details:
            result["details"] = details

        return result

    def validate_file(self, file_path: Path, data: bytes) -> dict[str, Any]:
        """ファイルを検証し、検証結果を返す (公開API).

        外部CLI (`uv run verify-metadata` など) からファイル検証を
        実行するための公開インターフェース。

        Args:
            file_path: 検証するファイルのパス
            data: ファイルのデータ (バイト形式)

        Returns:
            検証結果の辞書。以下のキーを含む:
            - status: "verified" または "failed"
            - verified_at: 検証日時 (ISO形式)
            - method: 検証方法 ("automated")
            - checks: 各検証項目の結果 (file_size, encoding, csv_format, path_safety)
            - errors: エラーメッセージのリスト
            - warnings: 警告メッセージのリスト
        """
        return self._validate_saved_file(file_path, data)

    def _check_file_size_validation(self, data: bytes) -> dict[str, Any]:
        """ファイルサイズを検証する。

        Args:
            data: ファイルデータ(バイト形式)

        Returns:
            検証結果の辞書
        """
        result: dict[str, Any] = {"valid": True, "errors": [], "warnings": []}

        size_bytes = len(data)
        size_mb = size_bytes / (1024 * 1024)

        if size_bytes < VALIDATION_MIN_FILE_SIZE:
            result["errors"].append(
                f"[file_size] File too small: {size_bytes} bytes (minimum: {VALIDATION_MIN_FILE_SIZE})"
            )
            result["valid"] = False
        elif size_mb > VALIDATION_MAX_FILE_SIZE_MB:
            result["errors"].append(
                f"[file_size] File too large: {size_mb:.2f} MB (maximum: {VALIDATION_MAX_FILE_SIZE_MB} MB)"
            )
            result["valid"] = False
        elif size_mb > VALIDATION_MAX_FILE_SIZE_MB * VALIDATION_SIZE_WARNING_THRESHOLD:
            result["warnings"].append(
                f"[file_size] File size warning: {size_mb:.2f} MB "
                f"({VALIDATION_SIZE_WARNING_THRESHOLD:.0%} of maximum)"
            )

        return result

    def _check_encoding_validation(self, data: bytes) -> dict[str, Any]:
        """エンコーディングを検証する。

        Args:
            data: ファイルデータ(バイト形式)

        Returns:
            検証結果の辞書。成功時は "decoded_content" キーにデコード結果を含む。
            これにより、後続のCSV検証で再デコードを回避できる (パフォーマンス最適化)。
        """
        result: dict[str, Any] = {"valid": True, "errors": [], "decoded_content": None}

        try:
            # Shift_JISでデコードを試みる (デコード成功がエンコーディング検証)
            decoded = data.decode(EXPECTED_ENCODING)
            result["decoded_content"] = decoded
        except UnicodeDecodeError as e:
            result["errors"].append(f"[encoding] Decoding error (expected {EXPECTED_ENCODING}): {e!s}")
            result["valid"] = False
        except (OSError, MemoryError, ValueError) as e:
            result["errors"].append(f"[encoding] Failed to check encoding: {e!s}")
            result["valid"] = False

        return result

    def _check_csv_format_validation(self, data: bytes, decoded_content: str | None = None) -> dict[str, Any]:
        """CSVフォーマットを検証する。

        Args:
            data: ファイルデータ(バイト形式)
            decoded_content: 事前にデコードされたコンテンツ (パフォーマンス最適化用)。
                             指定された場合はこれを使用し、再デコードを回避する。

        Returns:
            検証結果の辞書 (警告メッセージは統一形式、詳細情報はdetailsフィールドに保存)
        """
        result: dict[str, Any] = {"valid": True, "errors": [], "warnings": [], "details": {}}

        try:
            # デコード済みコンテンツがあれば再利用、なければフォールバックでデコード
            if decoded_content is not None:
                content = decoded_content
            else:
                content = data.decode(EXPECTED_ENCODING, errors="replace")
            csv_reader = csv.reader(io.StringIO(content))

            line_count = 0
            column_counts: set[int] = set()
            max_columns = 0

            for row in csv_reader:
                line_count += 1
                column_count = len(row)
                column_counts.add(column_count)
                max_columns = max(max_columns, column_count)

                # 行数チェック(早期終了)
                if line_count > VALIDATION_MAX_LINE_COUNT:
                    result["errors"].append(f"[csv_format] Too many lines: >{VALIDATION_MAX_LINE_COUNT}")
                    result["valid"] = False
                    break

            # 検証
            if line_count < VALIDATION_MIN_LINE_COUNT:
                result["errors"].append(
                    f"[csv_format] Too few lines: {line_count} (minimum: {VALIDATION_MIN_LINE_COUNT})"
                )
                result["valid"] = False

            if max_columns > VALIDATION_MAX_COLUMN_COUNT:
                result["errors"].append(
                    f"[csv_format] Too many columns: {max_columns} (maximum: {VALIDATION_MAX_COLUMN_COUNT})"
                )
                result["valid"] = False
            elif max_columns < VALIDATION_MIN_COLUMN_COUNT:
                result["errors"].append(
                    f"[csv_format] Too few columns: {max_columns} (minimum: {VALIDATION_MIN_COLUMN_COUNT})"
                )
                result["valid"] = False

            # カラム数の一貫性チェック
            if len(column_counts) > 1:
                # 警告メッセージは統一形式 (v1.3.0)
                result["warnings"].append(CSV_FORMAT_INCONSISTENT_COLUMN_COUNT_MSG)
                # 詳細情報はdetailsフィールドに保存 (ソート済みリスト形式)
                result["details"]["column_counts"] = sorted(column_counts)

        except csv.Error as e:
            result["errors"].append(f"[csv_format] CSV format error: {e!s}")
            result["valid"] = False
        except (OSError, MemoryError) as e:
            # Note: UnicodeDecodeError is not caught because errors="replace" is used
            result["errors"].append(f"[csv_format] Failed to check CSV format: {e!s}")
            result["valid"] = False

        return result

    def _check_path_safety_validation(self, file_path: Path) -> dict[str, Any]:
        """パスの安全性を検証する(パストラバーサル攻撃対策)。

        Args:
            file_path: 検証するファイルパス

        Returns:
            検証結果の辞書

        Note:
            以下の攻撃を検出:
            - パストラバーサル攻撃 (../等による親ディレクトリへのアクセス)
            - シンボリックリンク攻撃 (シンボリックリンクを介した許可外パスへのアクセス)
            - 危険な文字を含むパス (シェルインジェクション対策)
        """
        result: dict[str, Any] = {"valid": True, "errors": []}

        try:
            # シンボリックリンクチェック (解決前に実施)
            # シンボリックリンクは許可外のディレクトリへのアクセスに悪用される可能性がある
            if file_path.is_symlink():
                result["errors"].append("[path_safety] Symbolic links not allowed for security reasons")
                result["valid"] = False
                return result  # 早期リターンでresolve()をスキップ

            # 絶対パスを解決 (strict=Trueでファイルが存在しない場合はFileNotFoundError)
            try:
                resolved_path = file_path.resolve(strict=True)
            except FileNotFoundError:
                # ファイルが存在しない場合はstrict=Falseで解決
                resolved_path = file_path.resolve(strict=False)

            # base_path内にあることを確認
            try:
                resolved_path.relative_to(self.base_path.resolve())
            except ValueError:
                result["errors"].append(
                    f"[path_safety] Path traversal detected: {resolved_path} not in {self.base_path}"
                )
                result["valid"] = False

            # ファイル名の危険な文字チェック
            # Note: resolve() + relative_to() でパストラバーサルは既に防止されている
            # ここではファイル名のみをチェックし、親ディレクトリの誤検知を防ぐ
            dangerous_patterns = ["|", "&", ";", "$", "`", "\x00"]
            filename = file_path.name
            for pattern in dangerous_patterns:
                if pattern in filename:
                    result["errors"].append(f"[path_safety] Dangerous pattern in filename: {pattern!r}")
                    result["valid"] = False

        except (OSError, ValueError, RuntimeError) as e:
            result["errors"].append(f"[path_safety] Failed to check path safety: {e!s}")
            result["valid"] = False

        return result

    def _check_path_safety_pre_save(self, file_path: Path) -> str | None:
        """保存前のパス安全性チェック (セキュリティクリティカル)。

        Args:
            file_path: 検証するファイルパス

        Returns:
            エラーメッセージ (問題がある場合)、問題がない場合はNone
        """
        path_safety_result = self._check_path_safety_validation(file_path)
        if not path_safety_result["valid"]:
            error_msg = "; ".join(path_safety_result.get("errors", ["Path safety check failed"]))
            logger.error(f"Path safety check failed, aborting save: {error_msg}")
            return error_msg
        return None

    def _truncate_messages(self, messages: list[str], max_count: int) -> list[str]:
        """メッセージリストを制限する。

        Args:
            messages: メッセージのリスト
            max_count: 最大件数

        Returns:
            制限されたメッセージリスト
        """
        truncated: list[str] = []
        for msg in messages[:max_count]:
            truncated_msg = msg[: MAX_MESSAGE_LENGTH - 3] + "..." if len(msg) > MAX_MESSAGE_LENGTH else msg
            truncated.append(truncated_msg)

        if len(messages) > max_count:
            truncated.append(f"... 他{len(messages) - max_count}件のメッセージ")

        return truncated

    def get_existing_files(self, data_type: str | None = None, year: int | None = None) -> list[Path]:
        """既存のCSVファイルを検索して取得する。

        Args:
            data_type: フィルタリングするデータタイプ(オプション)
            year: フィルタリングする年(オプション)

        Returns:
            条件に一致するファイルパスのリスト(ソート済み)

        Note:
            フラット構造のため、base_path直下のCSVファイルを検索する。
            年でのフィルタリングは正規表現で厳密に行う。
            `fetch-data --plan-only` lists files with the same `find_existing_files`.
        """
        return find_existing_files(self.base_path, data_type=data_type, year=year)

    def get_metadata(self, file_path: Path) -> dict[str, Any] | None:
        """指定されたファイルのメタデータを取得する。

        Args:
            file_path: メタデータを取得するファイルのパス

        Returns:
            メタデータ辞書(正規化済み)、存在しない場合はNone

        Note:
            メタデータファイルは.metadataディレクトリから読み込まれる。
            旧形式のメタデータは自動的に正規化される。
            TOCTOU脆弱性を回避するため、exists()チェックなしで直接オープンを試みる。
        """
        # メタデータは.metadataディレクトリから取得
        metadata_filename = f"{file_path.stem}.json"
        metadata_path = self.metadata_dir / metadata_filename

        try:
            with metadata_path.open() as f:
                metadata = json.load(f)
            # 旧形式メタデータの正規化
            return self._normalize_metadata(metadata)
        except FileNotFoundError:
            # ファイルが存在しない場合はNoneを返す (通常のケース)
            return None
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as e:
            logger.warning(f"Failed to load metadata: {e}")
            return None

    def _normalize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """メタデータを正規化し、新旧両形式の互換フィールドを提供する。

        Args:
            metadata: 正規化するメタデータ辞書

        Returns:
            正規化されたメタデータ辞書 (新旧両形式のアクセサを提供)

        Note:
            v1.0形式とv1.1形式の両方を正規化して統一的にアクセスできるようにする。
            旧形式のフィールドは後方互換性のため維持される。
        """
        # v1.1形式かどうかを判定 (temporalオブジェクトがあるかで判断)
        is_v1_1 = "temporal" in metadata

        if is_v1_1:
            # v1.1形式 → 旧形式の互換フィールドを追加
            temporal = metadata.get("temporal", {})
            metadata.setdefault("year", temporal.get("year"))
            metadata.setdefault("period", temporal.get("period"))
            metadata.setdefault("period_type", temporal.get("period_type"))

            metadata.setdefault("file_size", metadata.get("bytes"))
            metadata.setdefault("line_count", metadata.get("lines"))

            hash_info = metadata.get("hash", {})
            metadata.setdefault("sha256_hash", hash_info.get("value"))
            metadata.setdefault("checksum_algorithm", hash_info.get("algorithm", "sha256"))

            # created/modified → created_at/updated_at
            metadata.setdefault("created_at", metadata.get("created"))
            metadata.setdefault("updated_at", metadata.get("modified"))

            # _fetch から source_url を取得
            fetch_info = metadata.get("_fetch", {})
            metadata.setdefault("source_url", fetch_info.get("source_url"))
            metadata.setdefault("fetch_time", fetch_info.get("fetch_time_seconds"))
            metadata.setdefault("force_overwrite", fetch_info.get("force_overwrite", False))
            metadata.setdefault("save_all_zero", fetch_info.get("save_all_zero", False))

        else:
            # v1.0形式 → 旧形式の正規化
            # timestamp → created_at/updated_at の移行
            if "created_at" not in metadata:
                metadata["created_at"] = metadata.get("timestamp")
            if "updated_at" not in metadata:
                metadata["updated_at"] = metadata.get("timestamp")

            # row_count → line_count の移行 (後方互換性)
            if "line_count" not in metadata and "row_count" in metadata:
                metadata["line_count"] = metadata.pop("row_count")

            # checksum_algorithm のデフォルト
            if "checksum_algorithm" not in metadata:
                metadata["checksum_algorithm"] = "sha256"

            # v1.1形式の互換フィールドを追加
            metadata.setdefault("bytes", metadata.get("file_size"))
            metadata.setdefault("lines", metadata.get("line_count"))
            # else分岐に入る時点でtemporalは未定義(v1.0形式の前提)なので、互換フィールドを必ず構築する
            metadata["temporal"] = {
                "year": metadata.get("year"),
                "period": metadata.get("period"),
                "period_type": metadata.get("period_type"),
            }
            if "hash" not in metadata:
                metadata["hash"] = {
                    "algorithm": metadata.get("checksum_algorithm", "sha256"),
                    "value": metadata.get("sha256_hash", ""),
                }
            metadata.setdefault("created", metadata.get("created_at"))
            metadata.setdefault("modified", metadata.get("updated_at"))

        # 欠損フィールドは明示的にNone
        metadata.setdefault("metadata_version", None)
        metadata.setdefault("source_url", None)
        metadata.setdefault("line_count", None)
        metadata.setdefault("lines", None)
        metadata.setdefault("verification", None)

        return metadata
