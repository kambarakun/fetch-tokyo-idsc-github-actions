#!/usr/bin/env python3
"""メタデータファイルを新形式に一括マイグレーションするスクリプト.

バージョン対応のマイグレーションシステム。
新しいバージョンが追加された場合は、マイグレーション関数を追加するだけで対応可能。

Usage:
    # ドライラン (変更内容を表示するだけ)
    uv run migrate-metadata --dry-run

    # 実行
    uv run migrate-metadata

    # 詳細出力
    uv run migrate-metadata --verbose

    # 特定バージョンへのマイグレーション
    uv run migrate-metadata --target-version 1.0
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import re
import sys
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:  # pragma: no cover
    from typing import TypeAlias


class MigrationStats(TypedDict):
    """マイグレーション統計情報の型定義."""

    total: int
    migrated: int
    skipped: int
    errors: int
    target_version: str


if TYPE_CHECKING:  # pragma: no cover
    MigrationFunc: TypeAlias = Callable[[dict, Path | None], tuple[dict, list[str]]]

from src.managers.storage_manager import METADATA_VERSION  # noqa: E402
from src.utils.version import parse_version  # noqa: E402
from src.validators.quality_validator import QualityValidator  # noqa: E402

logger = logging.getLogger(__name__)


class MigrationRegistry:
    """マイグレーション関数のレジストリ.

    バージョン間のマイグレーションパスを管理する。

    Example:
        >>> registry = MigrationRegistry()
        >>> @registry.register(from_version=None, to_version="1.0")
        ... def migrate_to_v1_0(metadata, data_file):
        ...     return metadata, ["change1"]
        >>> registry.get_migration_path(None, "1.0")
        [(None, '1.0')]
    """

    def __init__(self) -> None:
        """レジストリを初期化する."""
        self._migrations: dict[tuple[str | None, str], MigrationFunc] = {}
        self._versions: set[str | None] = set()

    def register(self, from_version: str | None, to_version: str) -> Callable[[MigrationFunc], MigrationFunc]:
        """マイグレーション関数を登録するデコレータ.

        Args:
            from_version: 変換元バージョン (Noneは旧形式)
            to_version: 変換先バージョン

        Returns:
            デコレータ関数
        """

        def decorator(func: MigrationFunc) -> MigrationFunc:
            self._migrations[(from_version, to_version)] = func
            self._versions.add(from_version)
            self._versions.add(to_version)
            return func

        return decorator

    def get_migration_path(self, from_version: str | None, to_version: str) -> list[tuple[str | None, str]]:
        """マイグレーションパスを取得する.

        Args:
            from_version: 現在のバージョン
            to_version: 目標バージョン

        Returns:
            マイグレーションステップのリスト [(from, to), ...]

        Raises:
            ValueError: パスが見つからない場合
        """
        if from_version == to_version:
            return []

        # BFSでパスを探索
        queue: deque[list[tuple[str | None, str]]] = deque()
        visited: set[str | None] = {from_version}

        # 初期ノードからの遷移を追加
        for (src, dst), _ in self._migrations.items():
            if src == from_version:
                queue.append([(src, dst)])
                visited.add(dst)

        while queue:
            path = queue.popleft()
            current = path[-1][1]

            if current == to_version:
                return path

            for (src, dst), _ in self._migrations.items():
                if src == current and dst not in visited:
                    queue.append([*path, (src, dst)])
                    visited.add(dst)

        msg = f"No migration path from {from_version} to {to_version}"
        raise ValueError(msg)

    def migrate(
        self,
        metadata: dict,
        data_file: Path | None,
        target_version: str,
    ) -> tuple[dict, list[str]]:
        """メタデータを指定バージョンにマイグレーションする.

        Args:
            metadata: マイグレーション対象のメタデータ
            data_file: 対応するCSVファイルのパス
            target_version: 目標バージョン

        Returns:
            (マイグレーション後のメタデータ, 変更内容のリスト)

        Raises:
            ValueError: ダウングレードが指定された場合
        """
        current_version = metadata.get("metadata_version")
        all_changes: list[str] = []

        # ダウングレードを禁止
        if self.is_downgrade(current_version, target_version):
            msg = (
                f"Downgrade is not supported: {current_version} -> {target_version}. "
                f"Current version ({current_version}) is newer than target ({target_version})."
            )
            raise ValueError(msg)

        path = self.get_migration_path(current_version, target_version)

        result = metadata.copy()
        for from_ver, to_ver in path:
            func = self._migrations[(from_ver, to_ver)]
            result, changes = func(result, data_file)
            all_changes.extend(changes)
            logger.debug(f"Applied migration: {from_ver} -> {to_ver}")

        return result, all_changes

    @property
    def latest_version(self) -> str:
        """最新バージョンを取得する."""
        return METADATA_VERSION

    @property
    def supported_versions(self) -> list[str | None]:
        """サポートされているバージョンのリストを取得する."""

        def _sort_key(v: str | None) -> tuple[bool, tuple[int, ...] | tuple[()]]:
            return (v is None, parse_version(v) if v is not None else ())

        return sorted(self._versions, key=_sort_key)

    @staticmethod
    def compare_versions(v1: str | None, v2: str | None) -> int:
        """バージョンを比較する.

        Args:
            v1: 比較元バージョン (Noneは旧形式で最も古い)
            v2: 比較先バージョン

        Returns:
            v1 < v2 なら負、v1 == v2 なら0、v1 > v2 なら正
        """
        # Noneは最も古いバージョン
        if v1 is None and v2 is None:
            return 0
        if v1 is None:
            return -1
        if v2 is None:
            return 1

        # セマンティックバージョニング比較 (共通ユーティリティを使用)
        try:
            v1_tuple = parse_version(v1)
            v2_tuple = parse_version(v2)
        except ValueError as e:
            # parse_versionのエラーメッセージを明確化
            msg = f"Invalid version format: '{v1}' or '{v2}'. Expected format: '1.0' or '1.2.3'"
            raise ValueError(msg) from e

        if v1_tuple < v2_tuple:
            return -1
        if v1_tuple > v2_tuple:
            return 1
        return 0

    def is_downgrade(self, from_version: str | None, to_version: str | None) -> bool:
        """ダウングレードかどうかを判定する.

        Args:
            from_version: 現在のバージョン
            to_version: 目標バージョン

        Returns:
            ダウングレードの場合True
        """
        return self.compare_versions(from_version, to_version) > 0


# グローバルレジストリ
migration_registry = MigrationRegistry()


# =============================================================================
# ユーティリティ関数
# =============================================================================


def count_lines(data_file: Path) -> int | None:
    """CSVファイルの行数をカウントする.

    バッファリングを使用してメモリ効率を最適化。
    大きなファイルでもメモリを消費しない。

    Args:
        data_file: カウント対象のCSVファイルパス

    Returns:
        行数。ファイルが存在しない場合はNone。
    """
    if not data_file.exists():
        return None
    try:
        line_count = 0
        last_char = b""
        buffer_size = 65536  # 64KB バッファ
        with data_file.open("rb") as f:
            while True:
                buffer = f.read(buffer_size)
                if not buffer:
                    break
                line_count += buffer.count(b"\n")
                last_char = buffer[-1:]
        # 最後の行が改行で終わっていない場合は+1
        if last_char and last_char != b"\n":
            line_count += 1
    except OSError:
        return None
    else:
        return line_count


# =============================================================================
# マイグレーション関数の定義
# =============================================================================


@migration_registry.register(from_version=None, to_version="1.0")
def migrate_none_to_v1_0(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
    """旧形式 (バージョンなし) から v1.0 へのマイグレーション.

    変換内容:
    - metadata_version: "1.0" を追加
    - timestamp -> created_at / updated_at
    - row_count -> line_count (存在する場合)
    - checksum_algorithm: "sha256" を追加
    - source_url: None を追加
    - verification: None を追加
    - line_count: CSVファイルから計算して追加
    """
    changes: list[str] = []
    migrated = metadata.copy()

    # metadata_version の追加
    migrated["metadata_version"] = "1.0"
    changes.append("metadata_version: None -> 1.0")

    # timestamp -> created_at / updated_at (timestampは削除)
    timestamp = migrated.get("timestamp")
    if "created_at" not in migrated:
        migrated["created_at"] = timestamp
        changes.append(f"created_at: None -> {timestamp}")

    if "updated_at" not in migrated:
        migrated["updated_at"] = timestamp
        changes.append(f"updated_at: None -> {timestamp}")

    # 旧形式の timestamp フィールドを削除
    if "timestamp" in migrated:
        migrated.pop("timestamp")
        changes.append(f"timestamp: {timestamp} -> removed")

    # row_count -> line_count
    if "line_count" not in migrated:
        if "row_count" in migrated:
            migrated["line_count"] = migrated.pop("row_count")
            changes.append(f"row_count -> line_count: {migrated['line_count']}")
        elif data_file and data_file.exists():
            line_count = count_lines(data_file)
            migrated["line_count"] = line_count
            changes.append(f"line_count: calculated -> {line_count}")
        else:
            migrated["line_count"] = None
            changes.append("line_count: None (file not found)")

    # checksum_algorithm の追加
    if "checksum_algorithm" not in migrated:
        migrated["checksum_algorithm"] = "sha256"
        changes.append("checksum_algorithm: None -> sha256")

    # source_url の追加
    if "source_url" not in migrated:
        migrated["source_url"] = None
        changes.append("source_url: None (not available)")

    # verification の追加
    if "verification" not in migrated:
        migrated["verification"] = None
        changes.append("verification: None (not available)")

    return migrated, changes


@migration_registry.register(from_version="1.0", to_version="1.1.0")
def migrate_v1_0_to_v1_1_0(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
    """v1.0 から v1.1.0 へのマイグレーション.

    変換内容:
    - metadata_version: "1.0" -> "1.1.0"
    - name: ファイル名から生成 (URL-safe identifier)
    - path: 相対パスを追加
    - profile: "tokyo-idsc-raw" を追加
    - data_type: ファイル名から抽出
    - temporal: year/period/period_type を構造化
    - bytes: file_size から名前変更
    - hash: sha256_hash + checksum_algorithm を構造化
    - created: created_at から名前変更
    - modified: updated_at から名前変更
    - _fetch: source_url, verification などを構造化
    """
    changes: list[str] = []
    migrated: dict = {}

    # metadata_version
    migrated["metadata_version"] = "1.1.0"
    changes.append("metadata_version: 1.0 -> 1.1.0")

    # filename から情報を抽出
    filename = metadata.get("filename", "")
    stem = Path(filename).stem if filename else ""

    # 空のfilenameに対する防御的処理
    if not stem:
        # data_fileから推測するか、エラーにする
        if data_file and data_file.exists():
            stem = data_file.stem
            filename = data_file.name
            logger.warning(f"Empty filename in metadata, reconstructed from data_file: {filename}")
            changes.append(f"filename: reconstructed from data_file -> {filename}")
        else:
            # フォールバック: メタデータの他の情報から推測を試みる
            data_type = metadata.get("data_type", "unknown")
            year = metadata.get("year", 0)
            period = metadata.get("period", 0)
            if data_type and year and period:
                stem = f"{data_type}_{year}_{period:02d}"
                filename = f"{stem}.csv"
                logger.warning(f"Empty filename in metadata, reconstructed from temporal data: {filename}")
                changes.append(f"filename: reconstructed from temporal -> {filename}")
            else:
                # 完全なフォールバック
                stem = "unknown"
                filename = "unknown.csv"
                logger.error("Empty filename in metadata with insufficient data to reconstruct")
                changes.append("WARNING: filename was empty, set to 'unknown.csv'")

    # name (URL-safe identifier)
    migrated["name"] = stem
    changes.append(f"name: added -> {stem}")

    # filename
    migrated["filename"] = filename

    # path (raw/xxx.csv 形式)
    migrated["path"] = f"raw/{filename}"
    changes.append(f"path: added -> {migrated['path']}")

    # profile
    migrated["profile"] = "tokyo-idsc-raw"
    changes.append("profile: added -> tokyo-idsc-raw")

    # data_type と temporal をファイル名から抽出
    data_type, temporal = _extract_data_type_and_temporal(stem)
    migrated["data_type"] = data_type
    migrated["temporal"] = temporal
    changes.append(f"data_type: added -> {data_type}")
    changes.append(f"temporal: added -> {temporal}")

    # bytes (file_size から名前変更)
    file_size = metadata.get("file_size", 0)
    migrated["bytes"] = file_size
    changes.append(f"bytes: file_size -> {file_size}")

    # lines (line_count から移行)
    line_count = metadata.get("line_count")
    migrated["lines"] = line_count

    # hash (sha256_hash + checksum_algorithm を構造化)
    sha256_hash = metadata.get("sha256_hash", "")
    algorithm = metadata.get("checksum_algorithm", "sha256")

    # ハッシュ値が空の場合の処理
    if not sha256_hash:
        # data_fileが提供されている場合はハッシュを再計算
        if data_file and data_file.exists():
            sha256_hash = hashlib.sha256(data_file.read_bytes()).hexdigest()
            changes.append(f"hash: recalculated from file -> {sha256_hash[:16]}...")
        else:
            # ハッシュ値が空のまま (警告)
            changes.append("WARNING: hash value is empty (file not available for recalculation)")

    migrated["hash"] = {
        "algorithm": algorithm,
        "value": sha256_hash,
    }
    if sha256_hash and "recalculated" not in changes[-1]:
        changes.append("hash: sha256_hash/checksum_algorithm -> structured")

    # encoding
    encoding = metadata.get("encoding", "shift_jis")
    migrated["encoding"] = encoding

    # created (created_at から名前変更)
    created_at = metadata.get("created_at", metadata.get("timestamp", ""))
    migrated["created"] = created_at
    changes.append(f"created: created_at -> {created_at[:20]}...")

    # modified (updated_at から名前変更)
    updated_at = metadata.get("updated_at", created_at)
    migrated["modified"] = updated_at
    changes.append(f"modified: updated_at -> {updated_at[:20]}...")

    # sources (新規追加)
    source_url = metadata.get("source_url")
    if source_url:
        migrated["sources"] = [{"title": "Tokyo IDSC", "path": source_url}]
        changes.append("sources: added from source_url")
    else:
        migrated["sources"] = []

    # verification (既存を移行)
    verification = metadata.get("verification")
    if verification:
        migrated["verification"] = verification

    # _fetch (source_url や fetch_time などを構造化)
    fetch_info: dict = {
        "source_url": source_url,
        "fetch_time_seconds": metadata.get("fetch_time"),
        "force_overwrite": metadata.get("force_overwrite", False),
        "save_all_zero": metadata.get("save_all_zero", False),
    }
    # Noneの項目を削除
    fetch_info = {k: v for k, v in fetch_info.items() if v is not None}
    if not fetch_info:
        fetch_info = {"source_url": None}
    migrated["_fetch"] = fetch_info
    changes.append("_fetch: structured from fetch fields")

    return migrated, changes


def _extract_data_type_and_temporal(stem: str) -> tuple[str, dict]:
    """ファイル名(stem)から data_type と temporal を抽出する.

    Args:
        stem: 拡張子なしのファイル名

    Returns:
        (data_type, temporal) のタプル
    """
    parts = stem.split("_")

    # デフォルト値 (v1.1スキーマ準拠: year >= 2000, period >= 1)
    data_type = stem
    temporal = {"year": 2000, "period": 1, "period_type": "weekly"}

    try:
        if len(parts) >= 4:
            # sentinel_weekly_gender_2025_01 形式
            # notifiable_weekly_2025_01 形式
            year = int(parts[-2])
            period = int(parts[-1])

            # period_type を判定
            if "weekly" in stem:
                period_type = "weekly"
            elif "monthly" in stem:
                period_type = "monthly"
            else:
                period_type = "weekly"

            temporal = {
                "year": year,
                "period": period,
                "period_type": period_type,
            }

            # data_type は year_period 部分を除いた残り
            # sentinel_weekly_gender_2025_01 -> sentinel_weekly_gender
            data_type = "_".join(parts[:-2])

    except (ValueError, IndexError):
        pass

    return data_type, temporal


# =============================================================================
# マイグレーション実行関数
# =============================================================================

# v1.0 で必須となるフィールド
V1_0_REQUIRED_FIELDS: set[str] = {
    "metadata_version",
    "created_at",
    "updated_at",
    "checksum_algorithm",
    "source_url",
    "verification",
    "line_count",
}

# v1.1 で必須となるフィールド
V1_1_REQUIRED_FIELDS: set[str] = {
    "metadata_version",
    "name",
    "filename",
    "path",
    "profile",
    "data_type",
    "temporal",
    "bytes",
    "hash",
    "encoding",
    "created",
    "modified",
}

# v1.2 で必須となるフィールド (v1.1と同じ、qualityは任意)
V1_2_REQUIRED_FIELDS: set[str] = V1_1_REQUIRED_FIELDS.copy()


def _build_quality_metadata_for_v1_2(migrated: dict[str, Any], data_file: Path | None) -> tuple[dict[str, Any], str]:
    """v1.2.0向けqualityフィールドを構築する."""
    from datetime import UTC, datetime

    default_timestamp = datetime.now(UTC).isoformat()
    if data_file is None or not data_file.exists():
        return (
            {
                "validation_timestamp": default_timestamp,
                "validation_status": "skipped",
                "issues": [],
            },
            "quality: added (validation_status=skipped)",
        )

    data_type = migrated.get("data_type")
    if not isinstance(data_type, str):
        data_type = ""

    try:
        quality_validator = QualityValidator(data_file.parent)
        quality = quality_validator.validate(data_file.name, data_type, {})
    except (OSError, ValueError) as e:
        logger.warning("Failed to evaluate quality for %s during migration: %s", data_file.name, e)
        return (
            {
                "validation_timestamp": default_timestamp,
                "validation_status": "skipped",
                "issues": [],
            },
            "quality: added (validation_status=skipped)",
        )

    issues = quality.get("issues", [])
    status = "failed" if issues else "completed"
    return (
        {
            "validation_timestamp": quality.get("validation_timestamp", default_timestamp),
            "validation_status": status,
            "issues": issues,
        },
        f"quality: added (validation_status={status})",
    )


@migration_registry.register(from_version="1.1.0", to_version="1.2.0")
def migrate_v1_1_0_to_v1_2_0(metadata: dict, data_file: Path | None) -> tuple[dict, list[str]]:
    """v1.1.0 から v1.2.0 へのマイグレーション.

    変換内容:
    - metadata_version: "1.1.0" -> "1.2.0"
    - quality: 新規追加 (既存データでは検証スキップ)

    Args:
        metadata: マイグレーション対象のメタデータ辞書
        data_file: 対応するCSVファイルのパス (未使用)

    Returns:
        (マイグレーション後のメタデータ, 変更リスト)
    """
    migrated = metadata.copy()
    changes = []

    # バージョン更新
    if migrated.get("metadata_version") != "1.2.0":
        migrated["metadata_version"] = "1.2.0"
        changes.append("metadata_version: 1.1.0 -> 1.2.0")

    # quality フィールドの追加
    # 既存データも可能な範囲で品質検証を実行し、結果を記録
    if "quality" not in migrated:
        quality, change_message = _build_quality_metadata_for_v1_2(migrated, data_file)
        migrated["quality"] = quality
        changes.append(change_message)

    return migrated, changes


@migration_registry.register(from_version="1.2.0", to_version="1.3.0")
def migrate_v1_2_0_to_v1_3_0(metadata: dict, _data_file: Path | None) -> tuple[dict, list[str]]:
    """v1.2.0 から v1.3.0 へのマイグレーション.

    変換内容:
    - metadata_version: "1.2.0" -> "1.3.0"
    - verification.warnings: 警告メッセージを統一形式に変換
    - verification.details: カラム数情報を構造化して保存

    Args:
        metadata: マイグレーション対象のメタデータ辞書
        _data_file: 対応するCSVファイルのパス (未使用)

    Returns:
        (マイグレーション後のメタデータ, 変更リスト)
    """
    migrated = copy.deepcopy(metadata)
    changes = []

    # バージョン更新
    if migrated.get("metadata_version") != "1.3.0":
        migrated["metadata_version"] = "1.3.0"
        changes.append("metadata_version: 1.2.0 -> 1.3.0")

    # verification.warnings の変換
    verification = migrated.get("verification")
    if verification and isinstance(verification, dict):
        warnings = verification.get("warnings", [])
        if warnings and isinstance(warnings, list):
            new_warnings = []
            details_obj = verification.get("details")
            if isinstance(details_obj, dict):
                details = details_obj.copy()
            else:
                details = {}
                if details_obj is not None:
                    changes.append("verification.details: invalid type reset to {}")
            warnings_updated = False
            all_column_counts = []  # 全ての警告からカラム数を収集

            # 警告メッセージのパターン: "[csv_format] Inconsistent column count: {0, 1, 2, 10}"
            # 数値、カンマ、空白のみを許可する厳密なパターン
            # 空セット"{}"や末尾スペース"{0, 1, }"にも対応するため`*`を使用
            pattern = re.compile(r"\[csv_format\] Inconsistent column count: \{([0-9, ]*)\}")

            for warning in warnings:
                if not isinstance(warning, str):
                    new_warnings.append(warning)
                    continue

                match = pattern.match(warning)
                if match:
                    # 統一メッセージに変換
                    new_warnings.append("[csv_format] Inconsistent column count")

                    # カラム数情報を抽出して収集 (複数の警告から全て収集)
                    column_counts_str = match.group(1)
                    # "0, 1, 2, 10" のような文字列をパース
                    try:
                        # 空文字列の場合は空リスト
                        if column_counts_str.strip():
                            column_counts = [int(x.strip()) for x in column_counts_str.split(",") if x.strip()]
                        else:
                            column_counts = []

                        # 全ての警告からカラム数を収集
                        all_column_counts.extend(column_counts)
                        warnings_updated = True
                    except (ValueError, AttributeError) as e:
                        # パース失敗時は元のメッセージを保持し、warningログとchangesに記録
                        warning_preview = warning[:50] + "..." if len(warning) > 50 else warning
                        logger.warning(f"Failed to parse column counts from warning: {warning!r} - {e}")
                        changes.append(f"verification.warnings: parse failed for '{warning_preview}', kept original")
                        new_warnings.append(warning)
                else:
                    new_warnings.append(warning)

            if warnings_updated:
                verification["warnings"] = new_warnings
                # 全ての警告から収集したカラム数を重複除去してソート
                if all_column_counts:
                    details["column_counts"] = sorted(set(all_column_counts))
                    changes.append(
                        f"verification.warnings: normalized {len([w for w in new_warnings if 'Inconsistent column count' in w])} message(s), "
                        f"details.column_counts: {details['column_counts']}"
                    )
                if details:
                    verification["details"] = details

    return migrated, changes


def needs_migration(metadata: dict, target_version: str = METADATA_VERSION) -> bool:
    """メタデータがマイグレーションを必要とするかチェックする.

    Args:
        metadata: チェック対象のメタデータ辞書
        target_version: 目標バージョン

    Returns:
        マイグレーションが必要な場合True
    """
    current_version = metadata.get("metadata_version")

    # バージョンが異なる場合はマイグレーション必要
    if current_version != target_version:
        return True

    # バージョンが同じでも、必須フィールドが欠けている場合は必要
    if target_version == "1.0":
        missing = V1_0_REQUIRED_FIELDS - set(metadata.keys())
        return len(missing) > 0

    # v1.1.x 形式のチェック
    if target_version.startswith("1.1"):
        missing = V1_1_REQUIRED_FIELDS - set(metadata.keys())
        return len(missing) > 0

    # v1.2.x 形式のチェック
    if target_version.startswith("1.2"):
        missing = V1_2_REQUIRED_FIELDS - set(metadata.keys())
        return len(missing) > 0

    return False


def migrate_metadata(
    metadata: dict,
    data_file: Path | None = None,
    target_version: str = METADATA_VERSION,
) -> tuple[dict, list[str]]:
    """メタデータを指定バージョンにマイグレーションする.

    Args:
        metadata: マイグレーション対象のメタデータ辞書
        data_file: 対応するCSVファイルのパス (行数計算用)
        target_version: 目標バージョン

    Returns:
        (マイグレーション後のメタデータ, 変更内容のリスト)
    """
    return migration_registry.migrate(metadata, data_file, target_version)


def run_migration(
    metadata_dir: Path,
    data_dir: Path,
    target_version: str = METADATA_VERSION,
    dry_run: bool = False,
    verbose: bool = False,
) -> MigrationStats:
    """メタデータの一括マイグレーションを実行する.

    Args:
        metadata_dir: メタデータディレクトリのパス
        data_dir: データファイルディレクトリのパス
        target_version: 目標バージョン
        dry_run: Trueの場合、変更を保存しない
        verbose: Trueの場合、詳細なログを出力

    Returns:
        マイグレーション結果の統計情報
    """
    stats: MigrationStats = {
        "total": 0,
        "migrated": 0,
        "skipped": 0,
        "errors": 0,
        "target_version": target_version,
    }

    metadata_files = sorted(metadata_dir.glob("*.json"))
    # hash_index.json は除外
    metadata_files = [f for f in metadata_files if f.name != "hash_index.json"]

    stats["total"] = len(metadata_files)
    logger.info(f"Found {stats['total']} metadata files to check")
    logger.info(f"Target version: {target_version}")

    for metadata_path in metadata_files:
        try:
            with metadata_path.open() as f:
                metadata = json.load(f)

            if not needs_migration(metadata, target_version):
                stats["skipped"] += 1
                if verbose:
                    logger.debug(f"Skipped (already at target): {metadata_path.name}")
                continue

            # 対応するCSVファイルのパス (パストラバーサル対策)
            csv_filename = metadata.get("filename", metadata_path.stem + ".csv")
            # ファイル名のみを抽出してパストラバーサルを防止
            csv_filename = Path(csv_filename).name
            data_file = data_dir / csv_filename

            migrated, changes = migrate_metadata(metadata, data_file, target_version)

            if dry_run:
                logger.info(f"[DRY-RUN] Would migrate: {metadata_path.name}")
                for change in changes:
                    logger.info(f"  - {change}")
            else:
                with metadata_path.open("w") as f:
                    json.dump(migrated, f, indent=2, ensure_ascii=False)
                if verbose:
                    logger.info(f"Migrated: {metadata_path.name}")
                    for change in changes:
                        logger.info(f"  - {change}")

            stats["migrated"] += 1

        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            stats["errors"] += 1
            logger.exception(f"Error processing {metadata_path.name}")

    return stats


def main() -> int:
    """メイン関数."""
    parser = argparse.ArgumentParser(description="メタデータファイルをマイグレーションする (バージョン対応)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="変更を保存せずに、マイグレーション対象を表示する",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="詳細なログを出力する",
    )
    parser.add_argument(
        "--target-version",
        type=str,
        default=METADATA_VERSION,
        help=f"目標バージョン (default: {METADATA_VERSION})",
    )
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=Path("data/raw/.metadata"),
        help="メタデータディレクトリのパス (default: data/raw/.metadata)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/raw"),
        help="データファイルディレクトリのパス (default: data/raw)",
    )
    parser.add_argument(
        "--list-versions",
        action="store_true",
        help="サポートされているバージョンを表示して終了",
    )
    args = parser.parse_args()

    # ログ設定
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.list_versions:
        logger.info("Supported versions:")
        for ver in migration_registry.supported_versions:
            label = "(legacy)" if ver is None else ""
            logger.info(f"  - {ver or 'None'} {label}")
        logger.info(f"Latest version: {migration_registry.latest_version}")
        return 0

    if not args.metadata_dir.exists():
        logger.error(f"Metadata directory not found: {args.metadata_dir}")
        return 1

    if args.dry_run:
        logger.info("=== DRY RUN MODE ===")

    stats = run_migration(
        metadata_dir=args.metadata_dir,
        data_dir=args.data_dir,
        target_version=args.target_version,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    # 結果サマリー
    logger.info("=== Migration Summary ===")
    logger.info(f"Target version: {stats['target_version']}")
    logger.info(f"Total files:    {stats['total']}")
    logger.info(f"Migrated:       {stats['migrated']}")
    logger.info(f"Skipped:        {stats['skipped']}")
    logger.info(f"Errors:         {stats['errors']}")

    if args.dry_run and stats["migrated"] > 0:
        logger.info("\nTo apply changes, run without --dry-run")

    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
