#!/usr/bin/env python3
"""メタデータファイルを新形式に一括マイグレーションするスクリプト.

バージョン対応のマイグレーションシステム。
新しいバージョンが追加された場合は、マイグレーション関数を追加するだけで対応可能。

Usage:
    # ドライラン (変更内容を表示するだけ)
    uv run python scripts/migrate_metadata.py --dry-run

    # 実行
    uv run python scripts/migrate_metadata.py

    # 詳細出力
    uv run python scripts/migrate_metadata.py --verbose

    # 特定バージョンへのマイグレーション
    uv run python scripts/migrate_metadata.py --target-version 1.0
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeAlias

    MigrationFunc: TypeAlias = Callable[[dict, Path | None], tuple[dict, list[str]]]

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.managers.storage_manager import METADATA_VERSION

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

        try:
            path = self.get_migration_path(current_version, target_version)
        except ValueError:
            return metadata, []

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
        return sorted(self._versions, key=lambda v: (v is None, v or ""))

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

        # セマンティックバージョニング比較
        def parse_version(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError as e:
                msg = f"Invalid version format: '{v}'. Expected format: '1.0' or '1.2.3'"
                raise ValueError(msg) from e

        return (parse_version(v1) > parse_version(v2)) - (parse_version(v1) < parse_version(v2))

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

    # name (URL-safe identifier)
    migrated["name"] = stem
    changes.append(f"name: added -> {stem}")

    # filename
    migrated["filename"] = filename

    # path
    if data_file:
        # data_file から相対パスを構築
        try:
            # data_file は data/raw/xxx.csv の形式を想定
            # raw/xxx.csv 形式でパスを設定
            migrated["path"] = f"raw/{filename}"
        except (ValueError, AttributeError):
            migrated["path"] = f"raw/{filename}"
    else:
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
    migrated["hash"] = {
        "algorithm": algorithm,
        "value": sha256_hash,
    }
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
V1_0_REQUIRED_FIELDS = {
    "metadata_version",
    "created_at",
    "updated_at",
    "checksum_algorithm",
    "source_url",
    "verification",
    "line_count",
}

# v1.1 で必須となるフィールド
V1_1_REQUIRED_FIELDS = {
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
) -> dict:
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
    stats = {
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
