"""データ処理システム

UTF-8変換・CSV分割・正規化機能を提供するモジュール。
"""

import csv
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from src.models.metadata import METADATA_VERSION
from src.validators.quality_validator import QualityValidator

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
    """正規化結果を表すデータクラス

    Attributes:
        success: 処理が成功したかどうか
        source_path: 処理元ファイルパス
        output_files: 生成された出力ファイルのリスト(デフォルトは空リスト)
        error: エラーメッセージ(失敗時のみ)
    """

    success: bool
    source_path: Path | None = None
    output_files: list[Path] = field(default_factory=list)
    error: str | None = None


class DataProcessor:
    """データ処理を統合的に管理するクラス"""

    # クラス定数: マジックナンバー/ストリングを定数化
    HEADER_SEARCH_RANGE = 20  # ヘッダー行を探す範囲(行数)
    DISEASE_KEYWORDS: ClassVar[list[str]] = [
        "インフルエンザ",
        "ウイルス",
        "感染症",
        "球菌",
        "結膜",
    ]
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

        # データ品質バリデーター
        self.quality_validator = QualityValidator(self.raw_dir)

    def process_file(self, source_file: Path) -> NormalizationResult:
        """ファイルを処理(UTF-8変換 + 正規化を一度に実行)

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

            # Shift_JIS → UTF-8変換(メモリ上)
            with source_file.open("r", encoding="shift_jis", errors="replace") as f:
                content = f.read()

            lines = content.splitlines(keepends=True)

            # カテゴリに応じて正規化処理
            if metadata["category"] == "notifiable":
                return self._process_notifiable(lines, source_file, metadata)
            if metadata["category"] == "sentinel":
                return self._process_sentinel(lines, source_file, metadata)
            return NormalizationResult(success=False, error=f"Unknown category: {metadata['category']}")

        except (UnicodeDecodeError, OSError, KeyError, ValueError) as e:
            logger.exception(f"処理失敗: {source_file.name}")
            return NormalizationResult(success=False, source_path=source_file, error=str(e))

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

        # クロスデータセット整合性チェック
        logger.info("クロスデータセット整合性チェックを開始します...")
        self._verify_cross_dataset_consistency()

        return {"total": total, "succeeded": succeeded, "failed": failed, "errors": errors}

    def _process_notifiable(self, lines: list[str], source_file: Path, metadata: dict[str, Any]) -> NormalizationResult:
        """全数報告データの処理(シンプル)

        Args:
            lines: UTF-8変換済みの行リスト
            source_file: 元ファイルパス
            metadata: ファイルメタデータ

        Returns:
            正規化結果
        """
        start_time = time.time()
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

            processing_time = time.time() - start_time
            logger.info(f"全数報告処理成功: {source_file.name} → {output_filename}")

            # メタデータ記録
            self._log_processing(source_file, [output_file], metadata, processing_time)

            return NormalizationResult(success=True, source_path=source_file, output_files=[output_file])

        except (OSError, csv.Error, ValueError):
            logger.exception(f"全数報告処理失敗: {source_file.name}")
            return NormalizationResult(
                success=False, source_path=source_file, error="全数報告処理中にエラーが発生しました"
            )

    def _validate_medical_district_sections(
        self, gender_sections: list[dict[str, Any]], source_file: Path, metadata: dict[str, Any]
    ) -> NormalizationResult | None:
        """medical_districtデータの性別セクション妥当性検証

        Args:
            gender_sections: 検出された性別セクション
            source_file: 元ファイルパス
            metadata: ファイルメタデータ

        Returns:
            検証失敗時はエラーのNormalizationResult、成功時はNone
        """
        if metadata.get("aggregation") != "medical_district":
            return None

        has_male_or_female = any(
            section.get("gender") in (self.GENDER_MALE, self.GENDER_FEMALE) for section in gender_sections
        )
        if not has_male_or_female:
            logger.error(f"medical_districtデータに男性/女性セクションが存在しません(異常データ): {source_file.name}")
            return NormalizationResult(
                success=False,
                source_path=source_file,
                error="medical_districtデータに必須の性別セクション(男性/女性)が存在しません",
            )
        return None

    def _process_gender_sections(
        self, lines: list[str], gender_sections: list[dict[str, Any]], metadata: dict[str, Any]
    ) -> tuple[list[Path], Path | None, Path | None, Path | None, dict[Path, str]]:
        """性別セクションを処理してファイルを生成

        Args:
            lines: UTF-8変換済みの行リスト
            gender_sections: 検出された性別セクション
            metadata: ファイルメタデータ

        Returns:
            (output_files, male_file, female_file, total_file, gender_info)のタプル
            gender_info: {output_path: gender_suffix} の辞書
        """
        output_files: list[Path] = []
        male_file: Path | None = None
        female_file: Path | None = None
        total_file: Path | None = None
        gender_info: dict[Path, str] = {}

        for section in gender_sections:
            # medical_districtのtotalセクションはスキップ(元データに含まれない仕様)
            if metadata.get("aggregation") == "medical_district" and section.get("gender") == self.GENDER_TOTAL:
                logger.info(
                    "medical_districtのtotalセクションをスキップします(元データに男女合計が含まれていない仕様です)"
                )
                continue

            output_file = self._save_gender_section(lines, section, metadata)
            if output_file:
                output_files.append(output_file)
                gender = section.get("gender")
                gender_suffix = self._get_gender_suffix(gender)
                gender_info[output_file] = gender_suffix
                if gender == self.GENDER_MALE:
                    male_file = output_file
                elif gender == self.GENDER_FEMALE:
                    female_file = output_file
                elif gender == self.GENDER_TOTAL:
                    total_file = output_file

        return output_files, male_file, female_file, total_file, gender_info

    def _validate_total_file(self, total_file: Path | None, male_file: Path | None, female_file: Path | None) -> None:
        """totalファイルの妥当性検証

        Args:
            total_file: totalファイルパス
            male_file: maleファイルパス
            female_file: femaleファイルパス
        """
        if not (total_file and male_file and female_file):
            return

        # totalファイルが空の場合は警告のみ(生データを尊重し、計算で埋めない)
        try:
            is_empty = self._is_empty_data_file(total_file)
        except OSError:
            logger.warning(f"totalセクションの空判定に失敗しました(検証スキップ): {total_file.name}")
            return

        if is_empty:
            logger.warning(
                f"totalセクションが空です。生データを尊重してそのまま保存します。"
                f"(データ抽出エラーの可能性があります): {total_file.name}"
            )
            return

        # totalファイルが元データにある場合、計算結果と一致するか検証
        self._verify_total_calculation(male_file, female_file, total_file)

    def _process_sentinel(self, lines: list[str], source_file: Path, metadata: dict[str, Any]) -> NormalizationResult:
        """定点監視データの処理(複雑・性別分割)

        Args:
            lines: UTF-8変換済みの行リスト
            source_file: 元ファイルパス
            metadata: ファイルメタデータ

        Returns:
            正規化結果
        """
        start_time = time.time()
        try:
            # 性別セクションを検出
            gender_sections = self._detect_gender_sections(lines)

            if not gender_sections:
                # 性別セクションがない場合は、列形式の可能性があるので
                # そのまま保存(分割なし)
                return self._process_sentinel_simple(lines, source_file, metadata)

            # medical_districtの場合、male/femaleセクションの存在を検証
            validation_error = self._validate_medical_district_sections(gender_sections, source_file, metadata)
            if validation_error:
                return validation_error

            # 各性別セクションを処理
            output_files, male_file, female_file, total_file, gender_info = self._process_gender_sections(
                lines, gender_sections, metadata
            )

            if not output_files:
                return NormalizationResult(success=False, error="出力ファイルが生成されませんでした")

            # totalファイルの妥当性検証
            self._validate_total_file(total_file, male_file, female_file)

            processing_time = time.time() - start_time
            logger.info(f"定点監視処理成功: {source_file.name} → {len(output_files)}ファイル")

            # メタデータ記録
            self._log_processing(source_file, output_files, metadata, processing_time, gender_info)

            return NormalizationResult(success=True, source_path=source_file, output_files=output_files)

        except (OSError, csv.Error, ValueError):
            logger.exception(f"定点監視処理失敗: {source_file.name}")
            return NormalizationResult(
                success=False, source_path=source_file, error="定点監視処理中にエラーが発生しました"
            )

    def _process_sentinel_simple(
        self, lines: list[str], source_file: Path, metadata: dict[str, Any]
    ) -> NormalizationResult:
        """定点監視データの単純処理(性別が列形式の場合)

        Args:
            lines: UTF-8変換済みの行リスト
            source_file: 元ファイルパス
            metadata: ファイルメタデータ

        Returns:
            正規化結果
        """
        start_time = time.time()
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

            processing_time = time.time() - start_time
            logger.info(f"定点監視処理成功(単純): {source_file.name} → {output_filename}")

            # メタデータ記録
            self._log_processing(source_file, [output_file], metadata, processing_time)

            return NormalizationResult(success=True, source_path=source_file, output_files=[output_file])

        except (OSError, csv.Error, ValueError):
            logger.exception(f"定点監視処理失敗(単純): {source_file.name}")
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

        except (OSError, csv.Error, ValueError):
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

        # ヘッダー行を探す(疾病名が複数含まれる行)
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
            ファイル名サフィックス(male/female/total)
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

        except (KeyError, ValueError, AttributeError):
            logger.exception(f"メタデータ抽出失敗: {filename}")
            return None

    def _is_empty_data_file(self, file_path: Path) -> bool:
        """データファイルが空(ヘッダーのみ)かチェック

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
        """文字列を整数にパース(空文字列は0)

        Args:
            value: パースする文字列

        Returns:
            整数値(空文字列の場合は0)

        Raises:
            ValueError: 数値に変換できない場合 (検証スキップ用)
        """
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            return int(stripped)
        except ValueError:
            # '*'など数値でない値は警告を出してValueErrorを再送出 (検証スキップ)
            logger.warning(f"数値変換失敗: '{value}' - 検証スキップ")
            raise

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

            # ヘッダー行から検証対象外の列を特定
            skip_columns = set()
            if len(total_data) > 0:
                header = total_data[0]
                for j, col_name in enumerate(header):
                    # 「定点」を含む列は、定点医療機関数などのメタデータなので検証スキップ
                    # 「急性呼吸器感染症」(ARI)は年齢グループ化されており、
                    # 一部の年齢帯で'*'(非該当)が入る既知の仕様のため検証スキップ
                    if "定点" in col_name or "入院" in col_name or "急性呼吸器感染症" in col_name:
                        skip_columns.add(j)

            mismatches = []

            # データ行を検証(ヘッダーをスキップ)
            for i in range(1, len(male_data)):
                for j in range(1, min(len(male_data[i]), len(female_data[i]), len(total_data[i]))):
                    # スキップ対象の列は検証しない
                    if j in skip_columns:
                        continue

                    try:
                        male_val = self._parse_int(male_data[i][j])
                        female_val = self._parse_int(female_data[i][j])
                        total_val = self._parse_int(total_data[i][j])
                        calculated = male_val + female_val

                        # male=0, female=0, total>0 のパターンは検証スキップ(定点数などのメタデータ)
                        if male_val == 0 and female_val == 0 and total_val > 0:
                            continue

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
                # 不一致の程度に応じてログレベルを調整
                if len(mismatches) >= 10:
                    # 多数の不一致がある場合は WARNING(元データの品質問題の可能性)
                    logger.warning(
                        f"total検証: {len(mismatches)}件の不一致 in {total_file.name}(元データの品質問題の可能性)"
                    )
                else:
                    # 軽微な不一致は DEBUG レベル
                    logger.debug(f"total検証: {len(mismatches)}件の軽微な不一致 in {total_file.name}")
                    for mm in mismatches[:3]:  # 最初の3件のみログ出力
                        logger.debug(
                            f"  行{mm['row']}列{mm['col']}: male({mm['male']}) + female({mm['female']}) = {mm['calculated']}, total={mm['total']}"
                        )
            else:
                logger.info(f"total検証OK: {total_file.name} (male + female と一致)")

        except (OSError, csv.Error, ValueError, IndexError):
            logger.exception(f"total検証失敗: {total_file.name}")

    def _log_processing(
        self,
        source: Path,
        outputs: list[Path],
        metadata: dict[str, Any],
        processing_time: float = 0.0,
        gender_info: dict[Path, str] | None = None,
    ) -> None:
        """処理メタデータを各出力ファイルごとに記録

        Args:
            source: 変換元パス
            outputs: 出力ファイルパスのリスト
            metadata: ファイルメタデータ
            processing_time: 処理時間(秒)
            gender_info: 出力ファイルごとの性別情報 {output_path: gender}
        """
        # ソースファイルのハッシュを計算
        source_hash = self._calculate_hash(source)
        source_name = source.stem  # 拡張子なしのファイル名

        for output in outputs:
            if not output.exists():
                logger.warning(f"出力ファイルが存在しません(メタデータ生成スキップ): {output}")
                continue

            try:
                # 出力ファイルの情報を取得
                output_content = output.read_bytes()
                output_hash = hashlib.sha256(output_content).hexdigest()
                output_size = len(output_content)
                line_count = output_content.count(b"\n")
                if output_content and not output_content.endswith(b"\n"):
                    line_count += 1

                # 性別情報を取得
                gender = None
                if gender_info and output in gender_info:
                    gender = gender_info[output]

                # タイムスタンプ (UTC)
                timestamp_iso = datetime.now(UTC).isoformat()

                # 期間情報を構築
                period_type = metadata.get("frequency", "weekly")
                temporal = {
                    "year": int(metadata.get("year", 0)),
                    "period": int(metadata.get("period", 0)),
                    "period_type": period_type,
                }

                # データタイプを構築
                category = metadata.get("category", "")
                aggregation = metadata.get("aggregation", "")
                data_type = f"{category}_{period_type}_{aggregation}" if aggregation else f"{category}_{period_type}"

                # v1.2.0メタデータを構築
                # データ品質検証を実行
                processing_meta = {
                    "source_name": source_name,
                    "source_hash": source_hash,
                    "processing_time_seconds": processing_time,
                    "gender": gender,
                }
                quality = self.quality_validator.validate(source.name, data_type, processing_meta)

                meta = {
                    "metadata_version": METADATA_VERSION,
                    "name": output.stem,
                    "filename": output.name,
                    "path": str(output.relative_to(self.base_dir)),
                    "profile": "tokyo-idsc-processed",
                    "data_type": data_type,
                    "temporal": temporal,
                    "bytes": output_size,
                    "lines": line_count,
                    "hash": {
                        "algorithm": "sha256",
                        "value": output_hash,
                    },
                    "encoding": "utf-8",
                    "created": timestamp_iso,
                    "modified": timestamp_iso,
                    "sources": [
                        {
                            "title": source.name,
                            "path": str(source.relative_to(self.base_dir)),
                        }
                    ],
                    "_process": processing_meta,
                    "quality": quality,
                }

                # メタデータディレクトリを作成 (存在しない場合)
                metadata_dir = self.processed_dir / ".metadata"
                metadata_dir.mkdir(parents=True, exist_ok=True)

                # メタデータファイルを保存
                metadata_file = metadata_dir / f"{output.stem}.json"
                with metadata_file.open("w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)

                logger.debug(f"メタデータ保存: {metadata_file.name}")
            except (OSError, ValueError, TypeError) as e:
                # メタデータ書き込み失敗はデータ処理の成功に影響させない
                logger.warning(f"メタデータ保存失敗: {output.name} - {e}")
                logger.debug("メタデータ保存エラーの詳細:", exc_info=True)

    def _calculate_hash(self, file_path: Path) -> str:
        """ファイルのSHA256ハッシュを計算

        Args:
            file_path: ハッシュ計算対象のファイルパス

        Returns:
            SHA256ハッシュ文字列
        """
        sha256 = hashlib.sha256()
        with file_path.open("rb") as f:
            while chunk := f.read(65536):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _verify_cross_dataset_consistency(self) -> None:
        """異なる集計軸 (age/health_center) のtotal値が一致するか検証

        同じ期間・頻度のデータについて、age と health_center のtotal行を比較し、
        データの整合性をチェックする。不一致があれば警告ログを出力。

        注: medical_districtは元データにtotalセクションが含まれないため、
        整合性チェックの対象外とする。
        """
        # 処理済みファイルから期間情報を収集
        periods_to_check = self._collect_periods_for_verification()

        if not periods_to_check:
            logger.info("整合性チェック対象のファイルセットが見つかりませんでした")
            return

        logger.info(f"整合性チェック対象: {len(periods_to_check)}期間 (age vs health_center)")

        checked_count = 0
        error_count = 0

        for period_key, files in periods_to_check.items():
            # 必要なファイルが全て揃っているか確認
            if not all(f.exists() for f in files.values()):
                continue

            try:
                # 各データセットの合計行を抽出
                age_total_row = self._extract_total_row(files["age"])
                hc_total_row = self._extract_total_row(files["health_center"])

                if not all((age_total_row, hc_total_row)):
                    logger.debug(f"合計行が見つかりません: {period_key}")
                    continue

                # 各疾患列ごとに比較
                mismatches = []
                if len(age_total_row) != len(hc_total_row):
                    logger.debug(
                        f"列数不一致: {period_key} age_cols={len(age_total_row)} health_center_cols={len(hc_total_row)}"
                    )
                max_cols = min(len(age_total_row), len(hc_total_row))

                for col_idx in range(1, max_cols):  # 0列目は「合計」ラベルなのでスキップ
                    age_val = self._parse_int(age_total_row[col_idx])
                    hc_val = self._parse_int(hc_total_row[col_idx])

                    if age_val != hc_val:
                        mismatches.append({"col": col_idx, "age": age_val, "health_center": hc_val})

                checked_count += 1

                if mismatches:
                    error_count += 1
                    logger.warning(f"⚠️  整合性エラー: {period_key} - {len(mismatches)}列で不一致")
                    # 最初の3件の不一致を詳細表示
                    for mm in mismatches[:3]:
                        logger.warning(f"  列{mm['col']}: age={mm['age']}, health_center={mm['health_center']}")
                    if len(mismatches) > 3:
                        logger.warning(f"  ... 他{len(mismatches) - 3}列で不一致")
                else:
                    logger.info(f"✅ 整合性OK: {period_key}")

            except (OSError, csv.Error, ValueError, IndexError):
                # 整合性チェック自体は継続しつつ、原因調査のために例外情報は残す
                logger.exception(
                    f"整合性チェックエラー: {period_key}\n"
                    f"  age: {files.get('age', 'N/A')}\n"
                    f"  health_center: {files.get('health_center', 'N/A')}"
                )
                continue

        logger.info(f"整合性チェック完了: {checked_count}期間チェック済み、{error_count}件のエラー")

    def _collect_periods_for_verification(self) -> dict[str, dict[str, Path]]:
        """整合性チェック対象の期間とファイルを収集

        注: medical_districtは元データにtotalセクションが含まれないため対象外。
        age と health_center の2軸のみをチェック対象とする。

        Returns:
            {period_key: {aggregation: file_path}} の辞書
        """
        periods: dict[str, dict[str, Path]] = {}

        # 処理済みファイルをスキャン
        for file_path in self.processed_dir.glob("normalized_sentinel_*_total_*.csv"):
            # ファイル名から情報を抽出
            # 例: normalized_sentinel_weekly_age_total_2025_01.csv
            parts = file_path.stem.split("_")

            if len(parts) < 6:
                continue

            # normalized_sentinel_{frequency}_{aggregation}_total_{year}_{period}
            frequency = parts[2]
            aggregation = "_".join(parts[3:-3])  # total より前の部分
            year = parts[-2]
            period = parts[-1]

            # age と health_center のみを対象 (medical_districtは除外)
            if aggregation not in ["age", "health_center"]:
                continue

            period_key = f"{frequency}_{year}_{period}"

            if period_key not in periods:
                periods[period_key] = {}

            periods[period_key][aggregation] = file_path

        # 2つの集計軸 (age, health_center) が揃っているもののみを返す
        return {k: v for k, v in periods.items() if len(v) == 2}

    def _extract_total_row(self, file_path: Path) -> list[str] | None:
        """CSVファイルから「合計」行を抽出

        Args:
            file_path: CSVファイルのパス

        Returns:
            合計行のデータ (見つからない場合はNone)
        """
        try:
            data = self._read_csv_data(file_path)
        except (OSError, csv.Error, IndexError):
            logger.debug(f"合計行読み込みエラー: {file_path}")
            return None

        # 「合計」行を探す
        for row in data:
            if row and row[0] == "合計":
                return row

        return None
