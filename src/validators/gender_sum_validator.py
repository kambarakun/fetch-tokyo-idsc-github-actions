"""Gender sum consistency validator.

Validates that male + female = total in gender-disaggregated data.
"""

import csv
import subprocess
from pathlib import Path
from typing import Any


class GenderSumValidator:
    """Validator for gender sum consistency (male + female = total)."""

    def __init__(self, raw_data_dir: Path):
        """Initialize validator.

        Args:
            raw_data_dir: Path to raw data directory
        """
        self.raw_data_dir = Path(raw_data_dir)
        # Instance-level cache to avoid memory leaks with lru_cache
        self._file_cache: dict[Path, str | None] = {}

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

        # データタイプによって検証適用可否を判断
        # gender データなど、性別分割されていないデータは検証不要
        if "medical_district" in data_type or "health_center" in data_type or "age" in data_type:
            return self._validate_gender_sum(source_path, source_filename)

        return None

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

        errors: list[dict[str, Any]] = []
        total_rows = 0
        failed_rows = 0

        # 各行でmale + female = totalを検証
        for i, (male_row, female_row, total_row) in enumerate(
            zip(sections["male"], sections["female"], sections["total"], strict=False)
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

                    # 許容誤差 0.01 で検証
                    if abs(expected - total_val) > 0.01:
                        # エラーを記録 (行ごとに最初の不一致のみ)
                        if not any(e["location"] == location for e in errors):
                            # ヘッダーからカラム名を取得
                            header = self._get_header(source_path, "total")
                            col_name = header[col_idx] if col_idx < len(header) else f"col_{col_idx}"

                            errors.append(
                                {
                                    "location": location,
                                    "column": col_name,
                                    "row_index": i + 3,  # ヘッダー2行 + データ開始1行の分を調整
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

        This method caches the conversion result to avoid redundant iconv calls.
        Uses instance-level cache to prevent memory leaks.

        Args:
            source_path: Path to source CSV file

        Returns:
            UTF-8 converted file content or None if conversion failed
        """
        # Return cached result if available
        if source_path in self._file_cache:
            return self._file_cache[source_path]

        # Perform conversion
        result = subprocess.run(
            ["iconv", "-f", "SHIFT_JIS", "-t", "UTF-8", str(source_path)],
            capture_output=True,
            text=True,
            check=False,
        )

        # Cache and return result
        if result.returncode != 0:
            self._file_cache[source_path] = None
            return None

        self._file_cache[source_path] = result.stdout
        return result.stdout

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

        if not all([male_start, female_start, total_start]):
            return {"male": [], "female": [], "total": []}

        # データ行を抽出 (ヘッダーは2行後、データは3行後から)
        # 次のセクションまでのデータを取得
        male_data_start = male_start + 2
        female_data_start = female_start + 2
        total_data_start = total_start + 2

        # データ行数を推定 (次のセクション開始位置から逆算)
        data_row_count = female_start - male_data_start - 1

        male_rows = [
            row
            for row in rows[male_data_start : male_data_start + data_row_count]
            if row and row[0] and row[0] not in ["", "性別"]
        ]
        female_rows = [
            row
            for row in rows[female_data_start : female_data_start + data_row_count]
            if row and row[0] and row[0] not in ["", "性別"]
        ]
        total_rows = [
            row
            for row in rows[total_data_start : total_data_start + data_row_count]
            if row and row[0] and row[0] not in ["", "性別", "集計期間終了週"]
        ]

        return {"male": male_rows, "female": female_rows, "total": total_rows}

    def _get_header(self, source_path: Path, section: str = "total") -> list[str]:
        """Get header row from specified section.

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

        # セクションの開始位置を見つける
        section_map = {"male": "男性", "female": "女性", "total": "男女合計"}
        target_section = section_map.get(section, "男女合計")

        for i, line in enumerate(lines):
            if f'性別","{target_section}"' in line:
                # 次の非空行がヘッダー
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].strip() and lines[j].startswith('"",'):
                        reader = csv.reader([lines[j]])
                        return next(reader)

        return []

    def _build_result(
        self,
        source_filename: str,
        total_rows: int,
        failed_rows: int,
        errors: list[dict],
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
        # 最大10件までのサンプルを含め、超過した場合はtruncatedフラグを立てる
        max_samples = 10
        affected_locations = errors[:max_samples]
        truncated = len(errors) > max_samples

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
