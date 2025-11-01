"""ストレージ管理システム

東京都感染症データのファイル保存、メタデータ管理、Git操作を担当するモジュール。
フラットなディレクトリ構造でデータファイルを管理し、重複チェックや自動コミット機能を提供。
"""

import hashlib
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SaveResult:
    """ファイル保存操作の結果を表すデータクラス。

    Attributes:
        success: 保存操作が成功したかどうか
        file_path: 保存されたファイルのパス（成功時のみ）
        metadata_path: 保存されたメタデータファイルのパス（成功時のみ）
        error: エラーメッセージ（失敗時のみ）
        is_duplicate: 重複ファイルとして検出されたかどうか
    """

    success: bool
    file_path: Path | None = None
    metadata_path: Path | None = None
    error: str | None = None
    is_duplicate: bool = False


@dataclass
class CommitResult:
    """Git コミット操作の結果を表すデータクラス。

    Attributes:
        success: コミット操作が成功したかどうか
        commit_hash: 作成されたコミットのハッシュ値（成功時のみ）
        message: コミットメッセージまたはステータスメッセージ
        error: エラーメッセージ（失敗時のみ）
    """

    success: bool
    commit_hash: str | None = None
    message: str | None = None
    error: str | None = None


class GitHandler:
    """Git操作を処理するハンドラークラス。

    GitHub ActionsやローカルでのGit操作を抽象化し、
    自動コミット、ファイル追加、リポジトリチェックなどの機能を提供。

    Attributes:
        auto_commit: 自動コミットを有効にするかどうか
    """

    def __init__(self, auto_commit: bool = True):
        """GitHandlerを初期化する。

        Args:
            auto_commit: 自動コミット機能を有効にするかどうか（デフォルト: True）
        """
        self.auto_commit = auto_commit

    def is_git_repo(self) -> bool:
        """現在のディレクトリがGitリポジトリ内にあるかを確認する。

        Returns:
            Gitリポジトリ内の場合True、それ以外の場合False

        Note:
            エラーが発生した場合はFalseを返す（安全側に倒す）
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
            )
            return result.returncode == 0
        except Exception:
            return False

    def add_files(self, files: list[Path]) -> bool:
        """指定されたファイルをGitのステージングエリアに追加する。

        Args:
            files: 追加するファイルのパスのリスト

        Returns:
            全ファイルの追加に成功した場合True、失敗した場合False

        Note:
            存在しないファイルは自動的にスキップされる
        """
        try:
            file_paths = [str(f) for f in files if f.exists()]
            if not file_paths:
                return True

            subprocess.run(["git", "add"] + file_paths, capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to add files to git: {e.stderr}")
            return False

    def commit(self, message: str) -> CommitResult:
        """ステージングエリアの変更をコミットする。

        Args:
            message: コミットメッセージ

        Returns:
            コミット操作の結果を含むCommitResultオブジェクト

        Note:
            変更がない場合はコミットを作成せず、成功として扱う
        """
        try:
            # 変更があるか確認
            result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True, text=True, check=False)

            if result.returncode == 0:
                # 変更なし
                return CommitResult(success=True, message="No changes to commit")

            # コミット実行
            result = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True, check=True)

            # コミットハッシュ取得
            hash_result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)

            return CommitResult(success=True, commit_hash=hash_result.stdout.strip(), message=message)

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to commit: {e.stderr}")
            return CommitResult(success=False, error=e.stderr)

    def configure_user(self) -> bool:
        """GitHub Actions用のGitユーザー設定を行う。

        Returns:
            設定に成功した場合True、失敗した場合False

        Note:
            GitHub Actionsボットのユーザー名とメールアドレスを設定する
        """
        try:
            subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
            subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to configure git user: {e}")
            return False


class StorageManager:
    """データファイルとメタデータのストレージを管理するクラス。

    東京都感染症データの保存、重複チェック、メタデータ管理、
    Git自動コミットなどのストレージ関連機能を統合的に提供。

    Attributes:
        base_path: データ保存のベースディレクトリ
        config: ストレージ設定を含む辞書
        git_handler: Git操作を処理するハンドラー
        metadata_dir: メタデータファイルを保存するディレクトリ
        hash_index_file: ファイルハッシュインデックスのパス
        hash_index: ファイルハッシュとパスのマッピング
    """

    def __init__(self, base_path: Path, config: dict[str, Any]):
        """StorageManagerを初期化する。

        Args:
            base_path: データ保存のベースディレクトリ
            config: ストレージ設定を含む辞書
                - auto_commit: Git自動コミットを有効にするか（デフォルト: True）
                - commit_message_template: コミットメッセージテンプレート
                - その他のストレージ関連設定
        """
        self.base_path = Path(base_path)
        self.config = config
        self.git_handler = GitHandler(config.get("auto_commit", True))

        # ディレクトリ作成
        self.base_path.mkdir(parents=True, exist_ok=True)

        # メタデータ保存用ディレクトリ
        self.metadata_dir = self.base_path / ".metadata"
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

        # ハッシュインデックスファイル
        self.hash_index_file = self.metadata_dir / "hash_index.json"
        self.hash_index = self._load_hash_index()

    def organize_file_path(self, data_type: str, year: int, period: int, is_monthly: bool = False) -> Path:
        """フラットなディレクトリ構造でのファイルパス生成する。

        Args:
            data_type: データタイプ（例: 'sentinel_weekly_age'）
            year: 年（例: 2025）
            period: 期間（週番号または月番号）
            is_monthly: 月次データの場合True、週次データの場合False

        Returns:
            ファイルを保存するディレクトリパス（常にbase_path）

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
    ) -> SaveResult:
        """データファイルとメタデータを保存する。

        Args:
            data: 保存するデータ（バイト形式）
            data_type: データタイプ（例: 'sentinel_weekly_age'）
            year: 年（例: 2025）
            period: 期間（週番号または月番号）
            is_monthly: 月次データの場合True、週次データの場合False
            additional_metadata: 追加のメタデータ（オプション）

        Returns:
            保存操作の結果を含むSaveResultオブジェクト

        Note:
            - SHA256ハッシュで重複チェックを行う
            - 重複データは保存をスキップする
            - メタデータは.metadataディレクトリに別途保存される
        """
        # data_typeのバリデーション（セキュリティ対策）
        if not self._validate_data_type(data_type):
            error_msg = f"Invalid data_type: {data_type}. Contains invalid characters."
            logger.error(error_msg)
            return SaveResult(success=False, error=error_msg)

        try:
            # データハッシュ計算
            data_hash = hashlib.sha256(data).hexdigest()

            # 重複チェック
            if self.check_duplicates(data_hash):
                logger.info(f"Duplicate file detected (hash: {data_hash[:16]}...)")
                return SaveResult(success=True, is_duplicate=True)

            # ファイルパス生成
            dir_path = self.organize_file_path(data_type, year, period, is_monthly)

            # ファイル名生成（タイムスタンプなし、ゼロパディングあり）
            # データタイプ名に既にweekly/monthlyが含まれているため、period_typeは不要
            filename = f"{data_type}_{year}_{period:02d}.csv"
            file_path = dir_path / filename

            # CSVファイル保存(Shift_JISのまま)
            file_path.write_bytes(data)

            # メタデータ生成
            period_type = "monthly" if is_monthly else "weekly"
            metadata = {
                "filename": filename,
                "data_type": data_type,
                "year": year,
                "period": period,
                "period_type": period_type,
                "timestamp": datetime.now().isoformat(),
                "file_size": len(data),
                "sha256_hash": data_hash,
                "encoding": "shift_jis",
                "file_path": str(file_path.relative_to(self.base_path)),
            }

            if additional_metadata:
                metadata.update(additional_metadata)

            # メタデータは別ディレクトリに保存（.metadataディレクトリ）
            metadata_filename = f"{filename.replace('.csv', '.json')}"
            metadata_path = self.metadata_dir / metadata_filename

            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            # ハッシュインデックス更新
            self._update_hash_index(data_hash, str(file_path))

            logger.info(f"Saved file: {file_path}")

            return SaveResult(success=True, file_path=file_path, metadata_path=metadata_path)

        except Exception as e:
            logger.exception("Failed to save file")
            return SaveResult(success=False, error=str(e))

    def commit_changes(
        self, message: str | None = None, data_type: str | None = None, date_range: str | None = None
    ) -> CommitResult:
        """Git自動コミットを実行する。

        Args:
            message: コミットメッセージ（省略時は自動生成）
            data_type: データタイプ（メッセージ生成用）
            date_range: 日付範囲（メッセージ生成用）

        Returns:
            コミット操作の結果を含むCommitResultオブジェクト

        Note:
            - auto_commitが無効な場合はスキップされる
            - Gitリポジトリでない場合はスキップされる
            - 変更がない場合はコミットを作成しない
        """
        if not self.git_handler.auto_commit:
            logger.info("Auto commit is disabled. Skipping git commit.")
            return CommitResult(success=True, message="Auto commit disabled")

        if not self.git_handler.is_git_repo():
            logger.warning("Not a git repository. Skipping commit.")
            return CommitResult(success=True, message="Not a git repository")

        # メッセージ生成
        if not message:
            if data_type and date_range:
                template = self.config.get("commit_message_template", "データ更新: {data_type} - {date_range}")
                message = template.format(data_type=data_type, date_range=date_range)
            else:
                message = f"データ更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        # ファイル追加
        files_to_add = [self.base_path, self.metadata_dir]
        self.git_handler.add_files(files_to_add)

        # コミット
        return self.git_handler.commit(message)

    def check_duplicates(self, file_hash: str) -> bool:
        """ファイルハッシュで重複をチェックする。

        Args:
            file_hash: チェックするファイルのSHA256ハッシュ

        Returns:
            既に同じハッシュのファイルが存在する場合True、それ以外False
        """
        return file_hash in self.hash_index

    def _load_hash_index(self) -> dict[str, str]:
        """ハッシュインデックスをファイルから読み込む。

        Returns:
            ファイルハッシュとファイルパスのマッピング辞書

        Note:
            ファイルが存在しない場合や読み込みエラーの場合は空の辞書を返す
        """
        if self.hash_index_file.exists():
            try:
                with self.hash_index_file.open() as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load hash index: {e}")
        return {}

    def _update_hash_index(self, file_hash: str, file_path: str) -> None:
        """ハッシュインデックスを更新してファイルに保存する。

        Args:
            file_hash: ファイルのSHA256ハッシュ
            file_path: ファイルのパス（文字列）

        Note:
            保存に失敗した場合は警告ログを出力するが、処理は継続される
        """
        self.hash_index[file_hash] = file_path
        try:
            with self.hash_index_file.open("w") as f:
                json.dump(self.hash_index, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to update hash index: {e}")

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
        import re

        # 英数字とアンダースコアのみを許可
        pattern = re.compile(r"^[a-zA-Z0-9_]+$")
        return bool(pattern.match(data_type))

    def _get_month_from_week(self, year: int, week: int) -> int:
        """ISO週番号から対応する月を計算する。

        Args:
            year: 年
            week: ISO週番号（1-53）

        Returns:
            その週が属する月（1-12）

        Note:
            ISO 8601規格に基づいて計算を行う。
            週の始まりは月曜日として扱われる。
        """
        # ISO週番号から日付を計算
        jan4 = date(year, 1, 4)
        week_start = jan4 - timedelta(days=jan4.weekday())
        target_date = week_start + timedelta(weeks=week - 1)
        return target_date.month

    def get_existing_files(self, data_type: str | None = None, year: int | None = None) -> list[Path]:
        """既存のCSVファイルを検索して取得する。

        Args:
            data_type: フィルタリングするデータタイプ（オプション）
            year: フィルタリングする年（オプション）

        Returns:
            条件に一致するファイルパスのリスト（ソート済み）

        Note:
            フラット構造のため、base_path直下のCSVファイルを検索する。
            年でのフィルタリングは正規表現で厳密に行う。
        """
        pattern = "*.csv"

        # フラット構造なので常にベースパスから検索
        search_path = self.base_path
        files = list(search_path.glob(pattern))  # rglobではなくglobを使用

        if data_type:
            files = [f for f in files if data_type in f.name]

        if year:
            # 正規表現でより厳密に年をフィルタリング
            # 例: sentinel_weekly_2025_01.csv にマッチ
            year_pattern = re.compile(rf"_{year}_\d{{2}}\.csv$")
            files = [f for f in files if year_pattern.search(f.name)]

        return sorted(files)

    def get_metadata(self, file_path: Path) -> dict[str, Any] | None:
        """指定されたファイルのメタデータを取得する。

        Args:
            file_path: メタデータを取得するファイルのパス

        Returns:
            メタデータ辞書、存在しない場合はNone

        Note:
            メタデータファイルは.metadataディレクトリから読み込まれる
        """
        # メタデータは.metadataディレクトリから取得
        metadata_filename = f"{file_path.stem}.json"
        metadata_path = self.metadata_dir / metadata_filename

        if metadata_path.exists():
            try:
                with metadata_path.open() as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load metadata: {e}")

        return None

    def cleanup_old_files(self, days_to_keep: int = 365) -> int:
        """指定日数より古いファイルを削除する。

        Args:
            days_to_keep: 保持する日数（デフォルト: 365日）

        Returns:
            削除されたファイル数

        Note:
            メタデータのタイムスタンプを基準に判定を行う。
            対応するメタデータファイルも一緒に削除される。
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        deleted_count = 0

        for file_path in self.base_path.glob("*.csv"):
            metadata = self.get_metadata(file_path)

            if metadata:
                try:
                    file_date = datetime.fromisoformat(metadata["timestamp"])
                    if file_date < cutoff_date:
                        file_path.unlink()

                        # メタデータファイルも削除
                        metadata_filename = f"{file_path.stem}.json"
                        metadata_path = self.metadata_dir / metadata_filename
                        if metadata_path.exists():
                            metadata_path.unlink()

                        deleted_count += 1
                        logger.info(f"Deleted old file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to process file {file_path}: {e}")

        logger.info(f"Cleanup completed. Deleted {deleted_count} files.")
        return deleted_count

    def get_storage_stats(self) -> dict[str, Any]:
        """ストレージの統計情報を取得する。

        Returns:
            以下のキーを含む統計情報辞書:
                - total_files: 総ファイル数
                - total_size_bytes: 総ファイルサイズ（バイト）
                - total_size_mb: 総ファイルサイズ（MB）
                - file_types: データタイプ別の統計
                - year_stats: 年別の統計
                - hash_index_size: ハッシュインデックスのエントリ数

        Note:
            フラット構造でも年別統計はファイル名から抽出して計算する
        """
        total_files = 0
        total_size = 0
        file_types = {}
        year_stats = {}

        for file_path in self.base_path.glob("*.csv"):
            total_files += 1
            file_size = file_path.stat().st_size
            total_size += file_size

            # ファイルタイプ別統計
            for data_type in ["sentinel_weekly", "sentinel_monthly", "notifiable"]:
                if data_type in file_path.name:
                    if data_type not in file_types:
                        file_types[data_type] = {"count": 0, "size": 0}
                    file_types[data_type]["count"] += 1
                    file_types[data_type]["size"] += file_size
                    break

            # 年別統計（ファイル名から年を抽出）
            # 例: sentinel_weekly_2025_01.csv から 2025 を抽出
            year_match = re.search(r"_(\d{4})_\d{2}\.csv$", file_path.name)
            if year_match:
                year = int(year_match.group(1))
                if year not in year_stats:
                    year_stats[year] = {"count": 0, "size": 0}
                year_stats[year]["count"] += 1
                year_stats[year]["size"] += file_size

        return {
            "total_files": total_files,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "file_types": file_types,
            "year_stats": year_stats,
            "hash_index_size": len(self.hash_index),
        }
