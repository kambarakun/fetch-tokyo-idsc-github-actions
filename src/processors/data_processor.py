"""データ処理システム

UTF-8変換・CSV分割・正規化機能を提供するモジュール。
"""

import csv
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ConversionResult:
    """変換結果を表すデータクラス"""

    success: bool
    source_path: Path | None = None
    dest_path: Path | None = None
    error: str | None = None


@dataclass
class NormalizationResult:
    """正規化結果を表すデータクラス"""

    success: bool
    source_path: Path | None = None
    output_files: list[Path] | None = None
    error: str | None = None


class DataProcessor:
    """データ処理を統合的に管理するクラス"""

    # クラス定数: マジックナンバー/ストリングを定数化
    HEADER_SEARCH_RANGE = 20  # ヘッダー行を探す範囲（行数）
    DISEASE_KEYWORDS = ["インフルエンザ", "ウイルス", "感染症", "球菌", "結膜"]
    MIN_DISEASE_COUNT = 2  # ヘッダー行と判定する最小疾病数
    COMMENT_PREFIX = "*"  # 注釈行のプレフィックス
    GENDER_MALE = "男性"
    GENDER_FEMALE = "女性"
    GENDER_TOTAL = "男女合計"
    GENDER_MARKER = "性別"

    def __init__(self, base_dir: Path):
        """DataProcessorを初期化する。

        Args:
            base_dir: data/ディレクトリのパス
        """
        self.base_dir = Path(base_dir)
        self.raw_dir = self.base_dir / "raw"
        self.processed_dir = self.base_dir / "processed"

        # ディレクトリ作成
        self.processed_dir.mkdir(parents=True, exist_ok=True)

        # メタデータディレクトリ
        (self.processed_dir / ".metadata").mkdir(parents=True, exist_ok=True)

    def process_file(self, source_file: Path) -> NormalizationResult:
        """ファイルを処理（UTF-8変換 + 正規化を一度に実行）

        Args:
            source_file: raw/配下のファイルパス

        Returns:
            正規化結果
        """
        try:
            if not source_file.exists():
                return NormalizationResult(success=False, error=f"File not found: {source_file}")

            # ファイル名からメタデータを抽出
            metadata = self._extract_metadata_from_filename(source_file.name)
            if not metadata:
                return NormalizationResult(success=False, error=f"Invalid filename: {source_file.name}")

            # Shift_JIS → UTF-8変換（メモリ上）
            with source_file.open("r", encoding="shift_jis", errors="replace") as f:
                content = f.read()

            lines = content.splitlines(keepends=True)

            # カテゴリに応じて正規化処理
            if metadata["category"] == "notifiable":
                return self._process_notifiable(lines, source_file, metadata)
            if metadata["category"] == "sentinel":
                return self._process_sentinel(lines, source_file, metadata)
            return NormalizationResult(success=False, error=f"Unknown category: {metadata['category']}")

        except Exception:
            logger.exception(f"処理失敗: {source_file.name}")
            return NormalizationResult(success=False, source_path=source_file, error="処理中にエラーが発生しました")

    def process_all(self) -> dict[str, Any]:
        """raw/配下の全CSVを処理

        Returns:
            処理結果の統計情報
        """
        csv_files = list(self.raw_dir.glob("*.csv"))
        total = len(csv_files)
        succeeded = 0
        failed = 0
        errors = []

        logger.info(f"一括処理開始: {total}ファイル")

        for csv_file in csv_files:
            result = self.process_file(csv_file)
            if result.success:
                succeeded += 1
            else:
                failed += 1
                errors.append({"file": csv_file.name, "error": result.error})

        logger.info(f"一括処理完了: 成功={succeeded}, 失敗={failed}")

        return {"total": total, "succeeded": succeeded, "failed": failed, "errors": errors}

    def _process_notifiable(self, lines: list[str], source_file: Path, metadata: dict[str, Any]) -> NormalizationResult:
        """全数報告データの処理（シンプル）

        Args:
            lines: UTF-8変換済みの行リスト
            source_file: 元ファイルパス
            metadata: ファイルメタデータ

        Returns:
            正規化結果
        """
        try:
            # ファイル名: normalized_{type}_{frequency}_{year}_{period}.csv
            # 例: normalized_notifiable_weekly_2000_01.csv
            output_filename = (
                f"normalized_{metadata['category']}_{metadata['frequency']}_{metadata['year']}_{metadata['period']}.csv"
            )
            output_file = self.processed_dir / output_filename

            # データ開始行を探す
            data_start_idx = None
            for i, line in enumerate(lines):
                if "疾病名" in line or "病名" in line:
                    data_start_idx = i
                    break

            if data_start_idx is None:
                return NormalizationResult(success=False, error="データ開始行が見つかりません")

            # データ部分を抽出して保存
            data_lines = lines[data_start_idx:]
            with output_file.open("w", encoding="utf-8") as f:
                f.writelines(data_lines)

            logger.info(f"全数報告処理成功: {source_file.name} → {output_filename}")

            # ログ記録
            self._log_processing(source_file, [output_file], metadata)

            return NormalizationResult(success=True, source_path=source_file, output_files=[output_file])

        except Exception:
            logger.exception(f"全数報告処理失敗: {source_file.name}")
            return NormalizationResult(
                success=False, source_path=source_file, error="全数報告処理中にエラーが発生しました"
            )

    def _process_sentinel(self, lines: list[str], source_file: Path, metadata: dict[str, Any]) -> NormalizationResult:
        """定点監視データの処理（複雑・性別分割）

        Args:
            lines: UTF-8変換済みの行リスト
            source_file: 元ファイルパス
            metadata: ファイルメタデータ

        Returns:
            正規化結果
        """
        try:
            # 性別セクションを検出
            gender_sections = self._detect_gender_sections(lines)

            if not gender_sections:
                # 性別セクションがない場合は、列形式の可能性があるので
                # そのまま保存（分割なし）
                return self._process_sentinel_simple(lines, source_file, metadata)

            output_files = []
            male_file = None
            female_file = None
            total_file = None

            # 各性別セクションを処理
            for section in gender_sections:
                output_file = self._save_gender_section(lines, section, metadata)
                if output_file:
                    output_files.append(output_file)
                    gender = section["gender"]
                    if gender == self.GENDER_MALE:
                        male_file = output_file
                    elif gender == self.GENDER_FEMALE:
                        female_file = output_file
                    elif gender == self.GENDER_TOTAL:
                        total_file = output_file

            if not output_files:
                return NormalizationResult(success=False, error="出力ファイルが生成されませんでした")

            # totalファイルが空の場合、male + female で計算
            if total_file and male_file and female_file and self._is_empty_data_file(total_file):
                logger.info(f"totalファイルが空のため、male + female で計算します: {total_file.name}")
                self._calculate_total_from_gender(male_file, female_file, total_file)

            # totalファイルが元データにある場合、計算結果と一致するか検証
            if total_file and male_file and female_file and not self._is_empty_data_file(total_file):
                self._verify_total_calculation(male_file, female_file, total_file)

            logger.info(f"定点監視処理成功: {source_file.name} → {len(output_files)}ファイル")

            # ログ記録
            self._log_processing(source_file, output_files, metadata)

            return NormalizationResult(success=True, source_path=source_file, output_files=output_files)

        except Exception:
            logger.exception(f"定点監視処理失敗: {source_file.name}")
            return NormalizationResult(
                success=False, source_path=source_file, error="定点監視処理中にエラーが発生しました"
            )

    def _process_sentinel_simple(
        self, lines: list[str], source_file: Path, metadata: dict[str, Any]
    ) -> NormalizationResult:
        """定点監視データの単純処理（性別が列形式の場合）

        Args:
            lines: UTF-8変換済みの行リスト
            source_file: 元ファイルパス
            metadata: ファイルメタデータ

        Returns:
            正規化結果
        """
        try:
            # ファイル名: normalized_{type}_{frequency}_{aggregation}_{year}_{period}.csv
            # 例: normalized_sentinel_weekly_gender_2000_01.csv
            output_filename = f"normalized_{metadata['category']}_{metadata['frequency']}_{metadata['aggregation']}_{metadata['year']}_{metadata['period']}.csv"
            output_file = self.processed_dir / output_filename

            # データ開始行を探す
            data_start_idx = None
            for i, line in enumerate(lines):
                if "疾病名" in line or "病名" in line or "年齢区分" in line:
                    data_start_idx = i
                    break

            if data_start_idx is None:
                return NormalizationResult(success=False, error="データ開始行が見つかりません")

            # データ部分を抽出して保存
            data_lines = lines[data_start_idx:]
            with output_file.open("w", encoding="utf-8") as f:
                f.writelines(data_lines)

            logger.info(f"定点監視処理成功（単純）: {source_file.name} → {output_filename}")

            # ログ記録
            self._log_processing(source_file, [output_file], metadata)

            return NormalizationResult(success=True, source_path=source_file, output_files=[output_file])

        except Exception:
            logger.exception(f"定点監視処理失敗（単純）: {source_file.name}")
            return NormalizationResult(
                success=False, source_path=source_file, error="定点監視単純処理中にエラーが発生しました"
            )

    def _detect_gender_sections(self, lines: list[str]) -> list[dict[str, Any]]:
        """性別セクションを検出

        Args:
            lines: ファイル行のリスト

        Returns:
            性別セクション情報のリスト
        """
        sections = []

        for i, line in enumerate(lines):
            if self.GENDER_MARKER in line and "," in line:
                parts = [p.strip().strip('"') for p in line.split(",")]
                if len(parts) >= 2:
                    gender = parts[1]
                    if gender in [self.GENDER_MALE, self.GENDER_FEMALE, self.GENDER_TOTAL]:
                        sections.append({"gender": gender, "start_line": i})

        return sections

    def _save_gender_section(self, lines: list[str], section: dict[str, Any], metadata: dict[str, Any]) -> Path | None:
        """性別セクションを保存

        Args:
            lines: UTF-8変換済みの行リスト
            section: セクション情報
            metadata: ファイルメタデータ

        Returns:
            出力ファイルパス
        """
        try:
            gender = section["gender"]
            gender_suffix = self._get_gender_suffix(gender)

            # ファイル名: normalized_{type}_{frequency}_{aggregation}_{gender}_{year}_{period}.csv
            # 例: normalized_sentinel_weekly_age_male_2000_01.csv
            output_filename = f"normalized_{metadata['category']}_{metadata['frequency']}_{metadata['aggregation']}_{gender_suffix}_{metadata['year']}_{metadata['period']}.csv"
            output_file = self.processed_dir / output_filename

            # セクションのデータを抽出
            section_lines = self._extract_section_data(lines, section)

            if not section_lines:
                logger.warning(f"セクションデータなし: {gender}")
                return None

            # 保存
            with output_file.open("w", encoding="utf-8") as f:
                f.writelines(section_lines)

            logger.debug(f"セクション保存成功: {gender} → {output_filename}")

            return output_file

        except Exception:
            logger.exception(f"セクション保存失敗: {gender}")
            return None

    def _extract_section_data(self, lines: list[str], section: dict[str, Any]) -> list[str]:
        """セクションのデータ部分を抽出

        Args:
            lines: 全行
            section: セクション情報

        Returns:
            セクションのデータ行
        """
        start_idx = section["start_line"]

        # ヘッダー行を探す（疾病名が複数含まれる行）
        header_idx = None
        for i in range(start_idx, min(start_idx + self.HEADER_SEARCH_RANGE, len(lines))):
            line = lines[i]
            disease_count = sum(1 for keyword in self.DISEASE_KEYWORDS if keyword in line)
            if disease_count >= self.MIN_DISEASE_COUNT:
                header_idx = i
                break

        if header_idx is None:
            return []

        # データ行を抽出
        data_lines = [lines[header_idx]]

        for i in range(header_idx + 1, len(lines)):
            line = lines[i]

            # 空行や次のセクションのメタデータで終了
            if not line.strip():
                continue

            if self.GENDER_MARKER in line or "定点報告" in line or "集計期間" in line:
                break

            # 注釈行をスキップ
            if line.startswith(self.COMMENT_PREFIX):
                continue

            # データ行を追加
            data_lines.append(line)

            # 合計行で終了
            if line.startswith('"合計"') or line.startswith("合計"):
                break

        return data_lines

    def _get_gender_suffix(self, gender: str) -> str:
        """性別表示名をファイル名サフィックスに変換

        Args:
            gender: 性別表示名

        Returns:
            ファイル名サフィックス（male/female/total）
        """
        mapping = {self.GENDER_MALE: "male", self.GENDER_FEMALE: "female", self.GENDER_TOTAL: "total"}
        return mapping.get(gender, "unknown")

    def _extract_metadata_from_filename(self, filename: str) -> dict[str, Any] | None:
        """ファイル名からメタデータを抽出

        Args:
            filename: ファイル名

        Returns:
            メタデータ辞書
        """
        try:
            # notifiable_weekly_2025_01.csv
            if filename.startswith("notifiable_"):
                parts = filename.replace(".csv", "").split("_")
                return {"category": "notifiable", "frequency": parts[1], "year": parts[2], "period": parts[3]}

            # sentinel_weekly_gender_2025_01.csv
            # sentinel_monthly_health_center_2025_01.csv (aggregationが2単語の場合もある)
            if filename.startswith("sentinel_"):
                parts = filename.replace(".csv", "").split("_")
                # 最後の2つは必ず year と period
                year = parts[-2]
                period = parts[-1]
                # 2番目は frequency
                frequency = parts[1]
                # 残りの中間部分を全て aggregation として結合
                aggregation = "_".join(parts[2:-2])

                return {
                    "category": "sentinel",
                    "frequency": frequency,
                    "aggregation": aggregation,
                    "year": year,
                    "period": period,
                }

            return None

        except Exception:
            logger.exception(f"メタデータ抽出失敗: {filename}")
            return None

    def _is_empty_data_file(self, file_path: Path) -> bool:
        """データファイルが空（ヘッダーのみ）かチェック

        Args:
            file_path: チェック対象ファイル

        Returns:
            ヘッダーのみの場合True
        """
        with file_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        return len(lines) <= 1

    def _read_csv_data(self, file_path: Path) -> list[list[str]]:
        """CSVファイルを読み込む

        Args:
            file_path: CSVファイルのパス

        Returns:
            CSV行のリスト
        """
        with file_path.open("r", encoding="utf-8") as f:
            return list(csv.reader(f))

    def _parse_int(self, value: str) -> int:
        """文字列を整数にパース（空文字列は0）

        Args:
            value: パースする文字列

        Returns:
            整数値（空文字列の場合は0）
        """
        return int(value) if value.strip() else 0

    def _sum_rows(self, male_row: list[str], female_row: list[str]) -> list[str]:
        """男性と女性のデータ行を加算

        Args:
            male_row: 男性データの行
            female_row: 女性データの行

        Returns:
            合計データの行
        """
        if len(male_row) != len(female_row):
            logger.warning(f"行の列数が不一致: male={len(male_row)}, female={len(female_row)}")

        total_row = [male_row[0]]  # 最初の列（地域名や年齢区分など）
        for j in range(1, min(len(male_row), len(female_row))):
            male_val = self._parse_int(male_row[j])
            female_val = self._parse_int(female_row[j])
            total_row.append(str(male_val + female_val))
        return total_row

    def _calculate_total_from_gender(self, male_file: Path, female_file: Path, total_file: Path) -> None:
        """male + female でtotalを計算

        Args:
            male_file: 男性データファイル
            female_file: 女性データファイル
            total_file: 合計ファイル（上書きされる）
        """
        try:
            # ヘルパーメソッドでCSV読み込み
            male_data = self._read_csv_data(male_file)
            female_data = self._read_csv_data(female_file)

            if len(male_data) != len(female_data):
                logger.warning(
                    f"male と female の行数が一致しません: male={len(male_data)}, female={len(female_data)}, file={male_file.name}"
                )
                return

            # ヘッダー行
            total_data = [male_data[0]]

            # データ行をヘルパーメソッドで加算
            for i in range(1, len(male_data)):
                total_row = self._sum_rows(male_data[i], female_data[i])
                total_data.append(total_row)

            # totalファイルに書き込み
            with total_file.open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerows(total_data)

            logger.info(f"totalを計算しました: {total_file.name} (male + female)")

        except Exception:
            logger.exception(f"total計算失敗: {total_file.name}")

    def _verify_total_calculation(self, male_file: Path, female_file: Path, total_file: Path) -> None:
        """元データのtotalが male + female と一致するか検証

        Args:
            male_file: 男性データファイル
            female_file: 女性データファイル
            total_file: 合計ファイル
        """
        try:
            # ヘルパーメソッドでCSV読み込み
            male_data = self._read_csv_data(male_file)
            female_data = self._read_csv_data(female_file)
            total_data = self._read_csv_data(total_file)

            # データ行数チェック
            if len(male_data) != len(female_data) or len(male_data) != len(total_data):
                logger.warning(
                    f"行数不一致: male={len(male_data)}, female={len(female_data)}, total={len(total_data)}, file={total_file.name}"
                )
                return

            mismatches = []

            # データ行を検証（ヘッダーをスキップ）
            for i in range(1, len(male_data)):
                for j in range(1, min(len(male_data[i]), len(female_data[i]), len(total_data[i]))):
                    try:
                        male_val = self._parse_int(male_data[i][j])
                        female_val = self._parse_int(female_data[i][j])
                        total_val = self._parse_int(total_data[i][j])
                        calculated = male_val + female_val

                        if calculated != total_val:
                            mismatches.append(
                                {
                                    "row": i,
                                    "col": j,
                                    "male": male_val,
                                    "female": female_val,
                                    "total": total_val,
                                    "calculated": calculated,
                                }
                            )
                    except (ValueError, IndexError):
                        # 数値でない、またはインデックスエラーはスキップ
                        pass

            if mismatches:
                logger.warning(f"total検証: {len(mismatches)}件の不一致 in {total_file.name}")
                for mm in mismatches[:3]:  # 最初の3件のみログ出力
                    logger.warning(
                        f"  行{mm['row']}列{mm['col']}: male({mm['male']}) + female({mm['female']}) = {mm['calculated']}, total={mm['total']}"
                    )
            else:
                logger.info(f"total検証OK: {total_file.name} (male + female と一致)")

        except Exception:
            logger.exception(f"total検証失敗: {total_file.name}")

    def _log_processing(self, source: Path, outputs: list[Path], metadata: dict[str, Any]) -> None:
        """処理ログを記録

        Args:
            source: 変換元パス
            outputs: 出力ファイルパスのリスト
            metadata: メタデータ
        """
        log_file = self.processed_dir / ".metadata" / "processing_log.json"

        output_info = []
        for output in outputs:
            output_info.append(
                {
                    "path": str(output.relative_to(self.base_dir)),
                    "size_bytes": output.stat().st_size if output.exists() else 0,
                }
            )

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "source": str(source.relative_to(self.base_dir)),
            "outputs": output_info,
            "metadata": metadata,
            "success": True,
        }

        # 既存ログを読み込み
        if log_file.exists():
            with log_file.open("r", encoding="utf-8") as f:
                logs = json.load(f)
        else:
            logs = {"processing": []}

        logs["processing"].append(log_entry)

        # ログを保存
        with log_file.open("w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
