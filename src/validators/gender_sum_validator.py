"""Gender sum consistency validator.

Validates that male + female = total in gender-disaggregated data.
"""

import csv
import logging
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class GenderSumValidator:
    """Validator for gender sum consistency (male + female = total)."""

    # Maximum cache size to prevent memory issues in long-running processes
    _MAX_CACHE_SIZE = 100

    # Subprocess timeout for iconv conversion (seconds)
    _CONVERSION_TIMEOUT = 30

    # Floating point comparison tolerance for validation
    _TOLERANCE = 0.01

    # Maximum number of error samples to include in validation report
    _MAX_ERROR_SAMPLES = 10

    # Applicable data types for gender sum validation
    # These data types have gender-disaggregated data (male, female, total sections)
    # and require validation of male + female = total consistency
    _APPLICABLE_DATA_TYPES = frozenset(
        {
            "sentinel_weekly_medical_district",
            "sentinel_weekly_health_center",
            "sentinel_weekly_age",
            "sentinel_daily_medical_district",
            "sentinel_daily_health_center",
            "sentinel_daily_age",
        }
    )

    def __init__(self, raw_data_dir: Path):
        """Initialize validator.

        Args:
            raw_data_dir: Path to raw data directory
        """
        self.raw_data_dir = Path(raw_data_dir)
        # Instance-level LRU cache to avoid memory leaks with functools.lru_cache
        # OrderedDict provides LRU behavior when combined with move_to_end()
        self._file_cache: OrderedDict[Path, str | None] = OrderedDict()

    def validate(self, source_filename: str, data_type: str) -> dict | None:
        """Validate gender sum consistency for a source file.

        Args:
            source_filename: Name of the source file (e.g., "sentinel_weekly_age_2024_01.csv")
            data_type: Type of data (e.g., "sentinel_weekly_age")

        Returns:
            Validation result dict or None if validation not applicable
        """
        source_path = self.raw_data_dir / source_filename

        if not source_path.exists():
            return None

        # データタイプによって検証適用可否を厳密に判断
        # 定義されたセットに含まれるデータタイプのみを検証対象とする
        # gender データなど、性別分割されていないデータは検証不要
        if data_type in self._APPLICABLE_DATA_TYPES:
            return self._validate_gender_sum(source_path, source_filename)

        return None

    def clear_cache(self) -> None:
        """Clear the file content cache.

        This method should be called between batch processing operations
        to prevent memory buildup in long-running processes.
        """
        self._file_cache.clear()

    def _validate_gender_sum(self, source_path: Path, source_filename: str) -> dict:
        """Validate gender sum consistency for any gender-disaggregated data.

        This is a generic validation method that works for all data types
        (medical_district, health_center, age) without code duplication.

        Args:
            source_path: Path to source CSV file
            source_filename: Name of the source file

        Returns:
            Validation result dict conforming to v1.2.0 schema
        """
        sections = self._extract_gender_sections(source_path)

        if not all(sections.values()):
            return self._no_validation_result(source_filename)

        # データ整合性チェック: 全セクションの行数が一致しているか
        male_rows_count = len(sections["male"])
        female_rows_count = len(sections["female"])
        total_rows_count = len(sections["total"])

        if male_rows_count != female_rows_count or male_rows_count != total_rows_count:
            logger.warning(
                "Row count mismatch in %s: male=%d, female=%d, total=%d",
                source_filename,
                male_rows_count,
                female_rows_count,
                total_rows_count,
            )
            return {
                "check_type": "gender_sum_consistency",
                "validation_status": "skipped",
                "message": f"Validation skipped: row count mismatch (male={male_rows_count}, female={female_rows_count}, total={total_rows_count})",
                "details": {
                    "source_file": source_filename,
                    "affected_count": 0,
                    "truncated": False,
                    "affected_locations": [],
                },
            }

        errors: list[dict[str, Any]] = []
        total_rows = 0
        failed_rows = 0

        # 各行でmale + female = totalを検証
        # strict=True を使用してデータ不整合を確実に検出
        for i, (male_row, female_row, total_row) in enumerate(
            zip(sections["male"], sections["female"], sections["total"], strict=True)
        ):
            total_rows += 1
            # 最初の列はlocation識別子 (医療圏名、保健所名、年齢群など)
            location = male_row[0] if male_row else f"row_{i}"

            # 数値列をチェック (最初の列は識別子なので1から)
            for col_idx in range(1, min(len(male_row), len(female_row), len(total_row))):
                # 空文字列の列はスキップ (定点数など、非数値データ)
                if not male_row[col_idx] or not female_row[col_idx]:
                    continue

                try:
                    male_val = float(male_row[col_idx])
                    female_val = float(female_row[col_idx])
                    total_val = float(total_row[col_idx]) if total_row[col_idx] else 0

                    expected = male_val + female_val

                    # 許容誤差で検証
                    if abs(expected - total_val) > self._TOLERANCE:
                        # エラーを記録 (行ごとに最初の不一致のみ)
                        if not any(e["location"] == location for e in errors):
                            # ヘッダーからカラム名を取得
                            header = self._get_header(source_path, "total")
                            col_name = header[col_idx] if col_idx < len(header) else f"col_{col_idx}"

                            errors.append(
                                {
                                    "location": location,
                                    "column": col_name,
                                    # セクション開始行(性別ヘッダー)からのオフセット: 性別行(0) + 空行(1) + カラムヘッダー(2) + データ行i(i) → i+3
                                    "row_index": i + 3,
                                    "male": int(male_val),
                                    "female": int(female_val),
                                    "total": int(total_val),
                                    "expected": int(expected),
                                }
                            )
                            failed_rows += 1
                        break
                except ValueError:
                    # 数値変換エラーは無視 (非数値データの場合)
                    continue

        return self._build_result(source_filename, total_rows, failed_rows, errors, "record(s)")

    def _read_source_file(self, source_path: Path) -> str | None:
        """Read and convert source file from Shift_JIS to UTF-8 with caching.

        This method implements LRU caching with size limit to prevent memory issues.
        The cache uses OrderedDict to maintain insertion order and implements LRU
        eviction when the cache exceeds _MAX_CACHE_SIZE.

        Security: Path traversal protection is enforced by checking that the path
        is relative to raw_data_dir.

        Args:
            source_path: Path to source CSV file

        Returns:
            UTF-8 converted file content or None if conversion failed
        """
        # Security: Prevent path traversal attacks
        try:
            source_path.resolve().relative_to(self.raw_data_dir.resolve())
        except ValueError:
            logger.error(
                "Path traversal attempt detected: %s is not within %s",
                source_path,
                self.raw_data_dir,
            )
            return None

        # Return cached result if available (and move to end for LRU)
        if source_path in self._file_cache:
            # Move to end to mark as recently used
            self._file_cache.move_to_end(source_path)
            return self._file_cache[source_path]

        # Perform conversion
        # Use "--" to prevent filenames starting with "-" from being interpreted as options
        # Add timeout to prevent hanging on large files or system issues
        try:
            result = subprocess.run(
                ["iconv", "-f", "SHIFT_JIS", "-t", "UTF-8", "--", str(source_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=self._CONVERSION_TIMEOUT,
                shell=False,  # Explicit security best practice
            )
        except subprocess.TimeoutExpired:
            logger.error(
                "Timeout while converting %s from Shift_JIS to UTF-8 (exceeded %d seconds)",
                source_path.name,
                self._CONVERSION_TIMEOUT,
            )
            self._cache_put(source_path, None)
            return None

        # Log conversion errors for debugging
        if result.returncode != 0:
            logger.warning(
                "Failed to convert %s from Shift_JIS to UTF-8: %s",
                source_path.name,
                result.stderr.strip() if result.stderr else "Unknown error",
            )
            self._cache_put(source_path, None)
            return None

        # Cache successful conversion
        content = result.stdout
        self._cache_put(source_path, content)
        return content

    def _cache_put(self, key: Path, value: str | None) -> None:
        """Put a value into the LRU cache, evicting oldest if necessary.

        Args:
            key: Cache key (file path)
            value: Cache value (file content or None)
        """
        # Add to cache
        self._file_cache[key] = value

        # Evict oldest entry if cache is too large
        if len(self._file_cache) > self._MAX_CACHE_SIZE:
            # FIFO: remove first (oldest) entry
            self._file_cache.popitem(last=False)

    def _extract_gender_sections(self, source_path: Path) -> dict[str, list[list[str]]]:
        """Extract male, female, and total sections from source file.

        Args:
            source_path: Path to source CSV file

        Returns:
            Dict with 'male', 'female', 'total' keys containing data rows
        """
        content = self._read_source_file(source_path)
        if content is None:
            return {"male": [], "female": [], "total": []}

        lines = content.split("\n")
        reader = csv.reader(lines)
        rows = list(reader)

        # セクションの開始位置を特定
        male_start = None
        female_start = None
        total_start = None

        for i, row in enumerate(rows):
            if len(row) > 1 and row[0] == "性別":
                if row[1] == "男性":
                    male_start = i
                elif row[1] == "女性":
                    female_start = i
                elif row[1] == "男女合計":
                    total_start = i

        # None 判定: 0 は有効な開始行のため、is None で判定する
        if any(x is None for x in (male_start, female_start, total_start)):
            return {"male": [], "female": [], "total": []}

        # 各セクションのデータ行を個別に抽出
        # 各セクションは構造が異なる可能性があるため、次のセクション開始位置まで個別に検出
        male_rows = self._extract_section_data(rows, male_start, female_start)
        female_rows = self._extract_section_data(rows, female_start, total_start)
        total_rows = self._extract_section_data(rows, total_start, None)

        return {"male": male_rows, "female": female_rows, "total": total_rows}

    def _extract_section_data(
        self, rows: list[list[str]], section_start: int, next_section_start: int | None
    ) -> list[list[str]]:
        """Extract data rows from a specific gender section.

        This method extracts data rows from a section, automatically detecting
        the section's end and filtering out metadata/footer rows.

        Args:
            rows: All CSV rows
            section_start: Starting line index of the section (性別 header line)
            next_section_start: Starting line index of next section, or None for last section

        Returns:
            List of data rows (excluding headers, footers, and metadata)
        """
        # データ開始位置 (セクションヘッダーの2行後から)
        data_start = section_start + 2

        # データ終了位置 (次のセクションの直前、または次のセクションがない場合はファイル終端)
        data_end = next_section_start - 1 if next_section_start is not None else len(rows)

        # フッター/メタデータ行を除外してデータ行のみ抽出
        # 除外対象:
        # - 空行 (row が空または row[0] が空)
        # - "性別" で始まる行 (セクションヘッダー)
        # - "集計期間開始週"、"集計期間終了週" (フッター行)
        # - "*" で始まる行 (注釈行)
        # - "定点報告疾患", "東京都", "定点数" などのフッター行
        excluded_prefixes = ["性別", "集計期間開始週", "集計期間終了週", "*", "定点報告疾患", "東京都", "定点数"]

        return [
            row
            for row in rows[data_start:data_end]
            if row and row[0] and row[0] != "" and not any(row[0].startswith(prefix) for prefix in excluded_prefixes)
        ]

    def _get_header(self, source_path: Path, section: str = "total") -> list[str]:
        """Get header row from specified section.

        This method uses CSV-parsed data instead of string matching to avoid
        dependencies on CSV quoting format and encoding details.

        Args:
            source_path: Path to source CSV file
            section: Section to get header from ('male', 'female', or 'total')

        Returns:
            List of column names
        """
        content = self._read_source_file(source_path)
        if content is None:
            return []

        lines = content.split("\n")
        reader = csv.reader(lines)
        rows = list(reader)

        # セクションの開始位置を見つける (CSV構造化データを使用)
        section_map = {"male": "男性", "female": "女性", "total": "男女合計"}
        target_section = section_map.get(section, "男女合計")

        for i, row in enumerate(rows):
            # CSVパース済みのデータで判定 (クォート形式に非依存)
            if len(row) >= 2 and row[0] == "性別" and row[1] == target_section:
                # 次の非空行がヘッダーを探す
                # 最大10行先まで検索: 注釈行(*で始まる)や空行が複数ある場合に対応
                # ageファイルでは注釈が3行+空行2行=5行あるため、余裕を持って10行検索
                for j in range(i + 1, min(i + 10, len(rows))):
                    # ヘッダー行は最初のセルが空文字列
                    if rows[j] and len(rows[j]) > 0 and rows[j][0] == "":
                        return rows[j]

        return []

    def _build_result(
        self,
        source_filename: str,
        total_rows: int,
        failed_rows: int,
        errors: list[dict[str, Any]],
        unit: str,
    ) -> dict:
        """Build validation result conforming to metadata schema v1.2.0.

        Args:
            source_filename: Source file name
            total_rows: Total number of rows checked
            failed_rows: Number of rows with errors
            errors: List of error details
            unit: Unit for error message (e.g., "record(s)")

        Returns:
            Validation result dict conforming to v1.2.0 schema
        """
        # v1.2.0: 問題なしの場合
        if failed_rows == 0:
            return {
                "check_type": "gender_sum_consistency",
                "validation_status": "completed",
                "message": f"No mismatch observed in {total_rows} {unit}",
                "details": {
                    "source_file": source_filename,
                    "affected_count": 0,
                    "truncated": False,
                    "affected_locations": [],
                },
            }
        # v1.2.0: 問題ありの場合
        # 最大サンプル数までのエラーを含め、超過した場合はtruncatedフラグを立てる
        affected_locations = errors[: self._MAX_ERROR_SAMPLES]
        truncated = len(errors) > self._MAX_ERROR_SAMPLES

        return {
            "check_type": "gender_sum_consistency",
            "validation_status": "completed",
            "message": f"Observed mismatch between (male + female) and reported total in {failed_rows} {unit}",
            "details": {
                "source_file": source_filename,
                "affected_count": failed_rows,
                "truncated": truncated,
                "affected_locations": affected_locations,
            },
        }

    def _no_validation_result(self, source_filename: str) -> dict:
        """Build result when validation cannot be performed (v1.2.0).

        Args:
            source_filename: Source file name

        Returns:
            Validation result dict conforming to v1.2.0 schema
        """
        return {
            "check_type": "gender_sum_consistency",
            "validation_status": "skipped",
            "message": "Validation skipped: source data not available or not applicable",
            "details": {
                "source_file": source_filename,
                "affected_count": 0,
                "truncated": False,
                "affected_locations": [],
            },
        }
