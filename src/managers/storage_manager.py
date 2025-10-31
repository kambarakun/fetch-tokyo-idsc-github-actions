"""
ストレージ管理システム
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class SaveResult:
    """保存結果"""
    success: bool
    file_path: Optional[Path] = None
    metadata_path: Optional[Path] = None
    error: Optional[str] = None
    is_duplicate: bool = False


@dataclass
class CommitResult:
    """Git コミット結果"""
    success: bool
    commit_hash: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


class GitHandler:
    """Git操作ハンドラー"""

    def __init__(self, auto_commit: bool = True):
        self.auto_commit = auto_commit

    def is_git_repo(self) -> bool:
        """Gitリポジトリかどうかを確認"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--is-inside-work-tree'],
                capture_output=True,
                text=True,
                check=False
            )
            return result.returncode == 0
        except Exception:
            return False

    def add_files(self, files: List[Path]) -> bool:
        """ファイルをGitに追加"""
        try:
            file_paths = [str(f) for f in files if f.exists()]
            if not file_paths:
                return True

            subprocess.run(
                ['git', 'add'] + file_paths,
                capture_output=True,
                text=True,
                check=True
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to add files to git: {e.stderr}")
            return False

    def commit(self, message: str) -> CommitResult:
        """変更をコミット"""
        try:
            # 変更があるか確認
            result = subprocess.run(
                ['git', 'diff', '--cached', '--quiet'],
                capture_output=True,
                check=False
            )

            if result.returncode == 0:
                # 変更なし
                return CommitResult(
                    success=True,
                    message="No changes to commit"
                )

            # コミット実行
            result = subprocess.run(
                ['git', 'commit', '-m', message],
                capture_output=True,
                text=True,
                check=True
            )

            # コミットハッシュ取得
            hash_result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                capture_output=True,
                text=True,
                check=True
            )

            return CommitResult(
                success=True,
                commit_hash=hash_result.stdout.strip(),
                message=message
            )

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to commit: {e.stderr}")
            return CommitResult(
                success=False,
                error=e.stderr
            )

    def configure_user(self):
        """GitHub Actions用のGitユーザー設定"""
        try:
            subprocess.run(
                ['git', 'config', 'user.name', 'github-actions[bot]'],
                check=True
            )
            subprocess.run(
                ['git', 'config', 'user.email', 'github-actions[bot]@users.noreply.github.com'],
                check=True
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to configure git user: {e}")
            return False


class StorageManager:
    """ストレージ管理クラス"""

    def __init__(self, base_path: Path, config: Dict[str, Any]):
        self.base_path = Path(base_path)
        self.config = config
        self.git_handler = GitHandler(config.get('auto_commit', True))

        # ディレクトリ作成
        self.base_path.mkdir(parents=True, exist_ok=True)

        # メタデータ保存用ディレクトリ
        self.metadata_dir = self.base_path / '.metadata'
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

        # ハッシュインデックスファイル
        self.hash_index_file = self.metadata_dir / 'hash_index.json'
        self.hash_index = self._load_hash_index()

    def organize_file_path(
        self,
        data_type: str,
        year: int,
        period: int,
        is_monthly: bool = False
    ) -> Path:
        """階層ディレクトリ構造でのファイルパス生成"""
        if is_monthly:
            # 月次データの場合
            dir_path = self.base_path / str(year) / f"{period:02d}"
        else:
            # 週次データの場合
            month = self._get_month_from_week(year, period)
            dir_path = self.base_path / str(year) / f"{month:02d}" / f"week_{period:02d}"

        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def save_with_metadata(
        self,
        data: bytes,
        data_type: str,
        year: int,
        period: int,
        is_monthly: bool = False,
        additional_metadata: Optional[Dict[str, Any]] = None
    ) -> SaveResult:
        """メタデータ付きファイル保存"""
        try:
            # データハッシュ計算
            data_hash = hashlib.sha256(data).hexdigest()

            # 重複チェック
            if self.check_duplicates(data_hash):
                logger.info(f"Duplicate file detected (hash: {data_hash[:16]}...)")
                return SaveResult(
                    success=True,
                    is_duplicate=True
                )

            # ファイルパス生成
            dir_path = self.organize_file_path(data_type, year, period, is_monthly)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # ファイル名生成
            period_type = "month" if is_monthly else "week"
            filename = f"{data_type}_{year}_{period}_{timestamp}.csv"
            file_path = dir_path / filename

            # CSVファイル保存(Shift_JISのまま)
            file_path.write_bytes(data)

            # メタデータ生成
            metadata = {
                'filename': filename,
                'data_type': data_type,
                'year': year,
                'period': period,
                'period_type': period_type,
                'timestamp': datetime.now().isoformat(),
                'file_size': len(data),
                'sha256_hash': data_hash,
                'encoding': 'shift_jis',
                'file_path': str(file_path.relative_to(self.base_path))
            }

            if additional_metadata:
                metadata.update(additional_metadata)

            # メタデータファイル保存
            metadata_filename = f"{filename.replace('.csv', '')}_metadata.json"
            metadata_path = dir_path / metadata_filename

            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            # ハッシュインデックス更新
            self._update_hash_index(data_hash, str(file_path))

            logger.info(f"Saved file: {file_path}")

            return SaveResult(
                success=True,
                file_path=file_path,
                metadata_path=metadata_path
            )

        except Exception as e:
            logger.exception("Failed to save file")
            return SaveResult(
                success=False,
                error=str(e)
            )

    def commit_changes(
        self,
        message: Optional[str] = None,
        data_type: Optional[str] = None,
        date_range: Optional[str] = None
    ) -> CommitResult:
        """Git自動コミット"""
        if not self.git_handler.auto_commit:
            logger.info("Auto commit is disabled. Skipping git commit.")
            return CommitResult(success=True, message="Auto commit disabled")

        if not self.git_handler.is_git_repo():
            logger.warning("Not a git repository. Skipping commit.")
            return CommitResult(success=True, message="Not a git repository")

        # メッセージ生成
        if not message:
            if data_type and date_range:
                message = self.config.get(
                    'commit_message_template',
                    'データ更新: {data_type} - {date_range}'
                ).format(data_type=data_type, date_range=date_range)
            else:
                message = f"データ更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        # ファイル追加
        files_to_add = [
            self.base_path,
            self.metadata_dir
        ]
        self.git_handler.add_files(files_to_add)

        # コミット
        return self.git_handler.commit(message)

    def check_duplicates(self, file_hash: str) -> bool:
        """重複ファイルチェック"""
        return file_hash in self.hash_index

    def _load_hash_index(self) -> Dict[str, str]:
        """ハッシュインデックスの読み込み"""
        if self.hash_index_file.exists():
            try:
                with open(self.hash_index_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load hash index: {e}")
        return {}

    def _update_hash_index(self, file_hash: str, file_path: str):
        """ハッシュインデックスの更新"""
        self.hash_index[file_hash] = file_path
        try:
            with open(self.hash_index_file, 'w') as f:
                json.dump(self.hash_index, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to update hash index: {e}")

    def _get_month_from_week(self, year: int, week: int) -> int:
        """週番号から月を取得"""
        # ISO週番号から日付を計算
        jan4 = date(year, 1, 4)
        week_start = jan4 - timedelta(days=jan4.weekday())
        target_date = week_start + timedelta(weeks=week-1)
        return target_date.month

    def get_existing_files(
        self,
        data_type: Optional[str] = None,
        year: Optional[int] = None
    ) -> List[Path]:
        """既存ファイルの取得"""
        pattern = "*.csv"

        if year:
            search_path = self.base_path / str(year)
        else:
            search_path = self.base_path

        files = list(search_path.rglob(pattern))

        if data_type:
            files = [f for f in files if data_type in f.name]

        return sorted(files)

    def get_metadata(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """ファイルのメタデータ取得"""
        metadata_path = file_path.parent / f"{file_path.stem}_metadata.json"

        if metadata_path.exists():
            try:
                with open(metadata_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load metadata: {e}")

        return None

    def cleanup_old_files(self, days_to_keep: int = 365):
        """古いファイルのクリーンアップ"""
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        deleted_count = 0

        for file_path in self.base_path.rglob("*.csv"):
            metadata = self.get_metadata(file_path)

            if metadata:
                try:
                    file_date = datetime.fromisoformat(metadata['timestamp'])
                    if file_date < cutoff_date:
                        file_path.unlink()

                        # メタデータファイルも削除
                        metadata_path = file_path.parent / f"{file_path.stem}_metadata.json"
                        if metadata_path.exists():
                            metadata_path.unlink()

                        deleted_count += 1
                        logger.info(f"Deleted old file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to process file {file_path}: {e}")

        logger.info(f"Cleanup completed. Deleted {deleted_count} files.")
        return deleted_count

    def get_storage_stats(self) -> Dict[str, Any]:
        """ストレージ統計情報の取得"""
        total_files = 0
        total_size = 0
        file_types = {}
        year_stats = {}

        for file_path in self.base_path.rglob("*.csv"):
            total_files += 1
            file_size = file_path.stat().st_size
            total_size += file_size

            # ファイルタイプ別統計
            for data_type in ['sentinel_weekly', 'sentinel_monthly', 'notifiable']:
                if data_type in file_path.name:
                    if data_type not in file_types:
                        file_types[data_type] = {'count': 0, 'size': 0}
                    file_types[data_type]['count'] += 1
                    file_types[data_type]['size'] += file_size
                    break

            # 年別統計
            parts = file_path.parts
            for part in parts:
                if part.isdigit() and len(part) == 4:
                    year = int(part)
                    if year not in year_stats:
                        year_stats[year] = {'count': 0, 'size': 0}
                    year_stats[year]['count'] += 1
                    year_stats[year]['size'] += file_size
                    break

        return {
            'total_files': total_files,
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'file_types': file_types,
            'year_stats': year_stats,
            'hash_index_size': len(self.hash_index)
        }