"""ストレージ管理システム

東京都感染症データのファイル保存、メタデータ管理、Git操作を担当するモジュール。
フラットなディレクトリ構造でデータファイルを管理し、重複チェックや自動コミット機能を提供。
"""

import csv
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.models.metadata import METADATA_VERSION

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
EXPECTED_ENCODING = "shift_jis"  # 期待されるエンコーディング

logger = logging.getLogger(__name__)


@dataclass
class SaveResult:
    """ファイル保存操作の結果を表すデータクラス。

    Attributes:
        success: 保存操作が成功したかどうか
        file_path: 保存されたファイルのパス(成功時のみ)
        metadata_path: 保存されたメタデータファイルのパス(成功時のみ)
        error: エラーメッセージ(失敗時のみ)
        is_duplicate: 重複ファイルとして検出されたかどうか
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


@dataclass
class CommitResult:
    """Git コミット操作の結果を表すデータクラス。

    Attributes:
        success: コミット操作が成功したかどうか
        commit_hash: 作成されたコミットのハッシュ値(成功時のみ)
        message: コミットメッセージまたはステータスメッセージ
        error: エラーメッセージ(失敗時のみ)
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
            auto_commit: 自動コミット機能を有効にするかどうか(デフォルト: True)
        """
        self.auto_commit = auto_commit

    def is_git_repo(self) -> bool:
        """現在のディレクトリがGitリポジトリ内にあるかを確認する。

        Returns:
            Gitリポジトリ内の場合True、それ以外の場合False

        Note:
            エラーが発生した場合はFalseを返す(安全側に倒す)
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

            subprocess.run(["git", "add", *file_paths], capture_output=True, text=True, check=True)
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
                - auto_commit: Git自動コミットを有効にするか(デフォルト: True)
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
            force_overwrite: 既存ファイルを強制的に上書きする場合True
            save_all_zero: 全て0のデータも保存する場合True(デフォルト: False)

        Returns:
            保存操作の結果を含むSaveResultオブジェクト

        Note:
            - SHA256ハッシュで重複チェックを行う
            - 重複データは保存をスキップする(force_overwriteがFalseの場合)
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
            # 優先度: 全て0チェック > 重複チェック
            # 理由: データ品質の問題は効率性の問題より優先される
            if not save_all_zero and self._is_all_zero_data(data):
                logger.info(
                    f"Skipping all-zero unpublished data: {data_type}_{year}_{period:02d} "
                    "(use --save-all-zero to save)"
                )
                return SaveResult(success=True, is_skipped=True)

            # 重複チェック(force_overwriteがFalseの場合のみ)
            if not force_overwrite and self.check_duplicates(data_hash):
                logger.info(f"Duplicate file detected (hash: {data_hash[:16]}...)")
                return SaveResult(success=True, is_duplicate=True)

            # ファイルパス生成
            dir_path = self.organize_file_path(data_type, year, period, is_monthly)

            # ファイル名生成(タイムスタンプなし、ゼロパディングあり)
            # データタイプ名に既にweekly/monthlyが含まれているため、period_typeは不要
            filename = f"{data_type}_{year}_{period:02d}.csv"
            file_path = dir_path / filename

            # パス安全性チェック(保存前に実行 - セキュリティクリティカル)
            # パストラバーサル攻撃等が検出された場合は保存を中断
            path_safety_error = self._check_path_safety_pre_save(file_path)
            if path_safety_error:
                return SaveResult(success=False, error=path_safety_error)

            # 新規ファイルかどうかを判定
            is_new_file = not file_path.exists()

            # 既存ファイルのチェック (force_overwriteの場合、古いハッシュを削除)
            if file_path.exists() and force_overwrite:
                self._handle_existing_file_overwrite(file_path)

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

            # 既存メタデータの取得 (force_overwrite時のcreated_at保持用)
            existing_metadata = self.get_metadata(file_path) if force_overwrite else None

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

            # メタデータは別ディレクトリに保存(.metadataディレクトリ)
            metadata_filename = f"{filename.replace('.csv', '.json')}"
            metadata_path = self.metadata_dir / metadata_filename

            with metadata_path.open("w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

            # ハッシュインデックス更新
            self._update_hash_index(data_hash, str(file_path))

            logger.info(f"Saved file: {file_path} (new={is_new_file})")

            return SaveResult(success=True, file_path=file_path, metadata_path=metadata_path, is_new=is_new_file)

        except Exception as e:
            logger.exception("Failed to save file")
            return SaveResult(success=False, error=str(e))

    def commit_changes(
        self, message: str | None = None, data_type: str | None = None, date_range: str | None = None
    ) -> CommitResult:
        """Git自動コミットを実行する。

        Args:
            message: コミットメッセージ(省略時は自動生成)
            data_type: データタイプ(メッセージ生成用)
            date_range: 日付範囲(メッセージ生成用)

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
                message = f"データ更新: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}"

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

    def _load_hash_index(self) -> dict[str, str | list[str]]:
        """ハッシュインデックスをファイルから読み込む。

        Returns:
            ファイルハッシュとファイルパス(単一または複数)のマッピング辞書

        Note:
            ファイルが存在しない場合や読み込みエラーの場合は空の辞書を返す
            後方互換性のため、古い形式(string)と新形式(list)の両方をサポート
        """
        if self.hash_index_file.exists():
            try:
                with self.hash_index_file.open() as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load hash index: {e}")
        return {}

    def _remove_from_hash_index(self, file_hash: str, file_path: str) -> None:
        """ハッシュインデックスから特定のファイルパスを削除する(ヘルパーメソッド)

        Args:
            file_hash: 削除対象のファイルハッシュ
            file_path: 削除対象のファイルパス
        """
        if file_hash not in self.hash_index:
            return

        current_entry = self.hash_index[file_hash]

        if isinstance(current_entry, str):
            # 単一ファイルの場合、パスが一致すれば削除
            if current_entry == file_path:
                del self.hash_index[file_hash]
        elif isinstance(current_entry, list) and file_path in current_entry:
            # 複数ファイルの場合、該当パスのみ削除
            current_entry.remove(file_path)
            # リストが空になったら、エントリ自体を削除
            if not current_entry:
                del self.hash_index[file_hash]
            # 1つだけ残ったら文字列に戻す(サイズ節約)
            elif len(current_entry) == 1:
                self.hash_index[file_hash] = current_entry[0]

        # ハッシュインデックスファイルを更新
        try:
            with self.hash_index_file.open("w") as f:
                json.dump(self.hash_index, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to update hash index after removal: {e}")
            # ハッシュインデックス更新失敗は重要なエラーとして扱う
            raise

    def _add_to_hash_index(self, file_hash: str, file_path: str) -> None:
        """ハッシュインデックスに新しいファイルを追加する。

        Args:
            file_hash: ファイルのSHA256ハッシュ
            file_path: ファイルのパス(文字列)

        Note:
            同じハッシュの複数ファイルをサポート(リスト形式で管理)
        """
        if file_hash not in self.hash_index:
            # 新規エントリは単一の文字列として保存(互換性とサイズ節約)
            self.hash_index[file_hash] = file_path
            return

        current = self.hash_index[file_hash]
        # 文字列の場合はリストに変換(後方互換性)
        if isinstance(current, str):
            if current != file_path:
                self.hash_index[file_hash] = [current, file_path]
        # リストの場合は追加
        elif isinstance(current, list) and file_path not in current:
            current.append(file_path)

    def _sort_hash_index_by_filename(self) -> dict[str, str | list[str]]:
        """ハッシュインデックスをファイル名順にソートする。

        Returns:
            ファイル名順にソートされたハッシュインデックス辞書
        """
        # ファイル名でソートするため、(ハッシュ, ファイルパス)のリストを作成
        items_to_sort = []
        for hash_key, file_paths in self.hash_index.items():
            paths_list = file_paths if isinstance(file_paths, list) else [file_paths]
            for path in paths_list:
                items_to_sort.append((hash_key, path))

        # ファイルパス(値)でソート
        items_to_sort.sort(key=lambda x: x[1])

        # ソート済みの順序で辞書を再構築
        sorted_index: dict[str, str | list[str]] = {}
        for hash_key, path in items_to_sort:
            if hash_key not in sorted_index:
                sorted_index[hash_key] = path
            else:
                current = sorted_index[hash_key]
                if isinstance(current, str):
                    sorted_index[hash_key] = [current, path]
                elif isinstance(current, list) and path not in current:
                    current.append(path)

        return sorted_index

    def _update_hash_index(self, file_hash: str, file_path: str) -> None:
        """ハッシュインデックスを更新してファイルに保存する。

        Args:
            file_hash: ファイルのSHA256ハッシュ
            file_path: ファイルのパス(文字列)

        Note:
            保存に失敗した場合は警告ログを出力するが、処理は継続される
            同じハッシュの複数ファイルをサポート(リスト形式で管理)
        """
        # インデックスに追加
        self._add_to_hash_index(file_hash, file_path)

        try:
            # ファイル名順にソート
            sorted_index = self._sort_hash_index_by_filename()

            with self.hash_index_file.open("w") as f:
                # sort_keys=Falseにして、挿入順序を保持(Python 3.7+では辞書は挿入順序を保持)
                json.dump(sorted_index, f, indent=2, ensure_ascii=False, sort_keys=False)

            # メモリ上のインデックスも更新 (ソート済みのものに置き換え)
            # これにより、同一セッション内での重複チェックなどが正しく動作する
            self.hash_index = sorted_index
        except Exception as e:
            logger.error(f"Failed to update hash index: {e}")
            # ハッシュインデックスの更新失敗は重要なエラーとして扱う
            raise

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
            content = data.decode("shift_jis", errors="replace")
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
            logger.exception("Failed to decode CSV data as Shift_JIS")
            return False
        except csv.Error:
            # CSV解析エラー - 安全側に倒して保存する
            logger.exception("Failed to parse CSV data")
            return False
        except Exception:
            # その他の予期しないエラー - 安全側に倒して保存する
            logger.exception("Unexpected error while checking for all-zero data")
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
            existing_metadata: 既存のメタデータ (force_overwrite時に取得)
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
        if size_result.get("details"):
            details.update(size_result["details"])

        # エンコーディングチェック
        encoding_result = self._check_encoding_validation(data)
        checks["encoding"] = encoding_result["valid"]
        errors.extend(encoding_result.get("errors", []))
        if encoding_result.get("details"):
            details.update(encoding_result["details"])

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
        if path_result.get("details"):
            details.update(path_result["details"])

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

        外部スクリプト (verify_metadata.py など) からファイル検証を
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

    def _handle_existing_file_overwrite(self, file_path: Path) -> None:
        """既存ファイルの上書き処理 (ハッシュインデックス更新)。

        Args:
            file_path: 上書きするファイルパス
        """
        old_data = file_path.read_bytes()
        old_hash = hashlib.sha256(old_data).hexdigest()
        self._remove_from_hash_index(old_hash, str(file_path))
        logger.info(f"Overwriting existing file: {file_path}")

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

    def _get_month_from_week(self, year: int, week: int) -> int:
        """ISO週番号から対応する月を計算する。

        Args:
            year: ISO週暦年(例: 2025年第1週の年は2025)
            week: ISO週番号(1-53)

        Returns:
            その週が属する月(1-12)

        Note:
            ISO 8601規格に基づいて計算を行う。
            週の始まりは月曜日として扱われる。
            年境界を正しく処理するため、date.fromisocalendar()を使用。

        Examples:
            >>> _get_month_from_week(2025, 1)  # 2025年第1週(2024/12/30-2025/1/5)→ 1月
            1
            >>> _get_month_from_week(2024, 52)  # 2024年第52週(2024/12/23-29)→ 12月
            12
        """
        # ISO週番号から日付を正確に計算(年境界考慮)
        # 月曜日(weekday=1)を週の始まりとする
        week_start = date.fromisocalendar(year, week, 1)
        return week_start.month

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
            if "temporal" not in metadata:
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

    def cleanup_old_files(self, days_to_keep: int = 365) -> int:
        """指定日数より古いファイルを削除する。

        Args:
            days_to_keep: 保持する日数(デフォルト: 365日)

        Returns:
            削除されたファイル数

        Note:
            メタデータのタイムスタンプを基準に判定を行う。
            対応するメタデータファイルも一緒に削除される。
        """
        cutoff_date = datetime.now(UTC) - timedelta(days=days_to_keep)
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
                - total_size_bytes: 総ファイルサイズ(バイト)
                - total_size_mb: 総ファイルサイズ(MB)
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

            # 年別統計(ファイル名から年を抽出)
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
