"""テストヘルパー関数とユーティリティ"""

import random
import string
from pathlib import Path


def create_test_csv_data(rows: int = 100, columns: int = 2, include_headers: bool = True, separator: str = ",") -> str:
    """テスト用CSVデータを生成する。

    Args:
        rows: 生成する行数
        columns: 生成する列数
        include_headers: ヘッダー行を含めるか
        separator: 区切り文字

    Returns:
        生成されたCSVデータ
    """
    lines = []

    if include_headers:
        headers = [f"col{i}" for i in range(columns)]
        lines.append(separator.join(headers))

    for row_idx in range(rows):
        row_data = [str(row_idx * columns + col_idx) for col_idx in range(columns)]
        lines.append(separator.join(row_data))

    return "\n".join(lines)


def create_random_data(size_bytes: int) -> str:
    """指定サイズのランダムデータを生成する。

    Args:
        size_bytes: 生成するデータのバイト数

    Returns:
        生成されたランダムデータ
    """
    # ASCII文字のランダム生成
    chars = string.ascii_letters + string.digits + "\n ,.!?"
    result = []
    current_size = 0

    while current_size < size_bytes:
        chunk = random.choice(chars)
        result.append(chunk)
        current_size += len(chunk.encode("utf-8"))

    return "".join(result)[:size_bytes]


def create_test_file(
    path: Path, content: str | None = None, size_bytes: int | None = None, encoding: str = "utf-8"
) -> Path:
    """テストファイルを作成する。

    Args:
        path: ファイルパス
        content: ファイル内容（指定されない場合はランダム生成）
        size_bytes: ファイルサイズ（contentが指定されない場合に使用）
        encoding: エンコーディング

    Returns:
        作成されたファイルのPath
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if content is None:
        if size_bytes:
            content = create_random_data(size_bytes)
        else:
            content = create_test_csv_data()

    path.write_text(content, encoding=encoding)
    return path


def create_test_metadata(
    data_type: str = "test",
    year: int = 2024,
    period: int = 1,
    is_monthly: bool = False,
    file_hash: str = "dummy_hash",
    **kwargs,
) -> dict:
    """テスト用メタデータを生成する。

    Args:
        data_type: データタイプ
        year: 年
        period: 期間
        is_monthly: 月次データか
        file_hash: ファイルハッシュ
        **kwargs: 追加のメタデータフィールド

    Returns:
        生成されたメタデータ辞書
    """
    metadata = {
        "data_type": data_type,
        "year": year,
        "period": period,
        "is_monthly": is_monthly,
        "file_hash": file_hash,
        "created_at": "2024-01-01T00:00:00",
        "file_size": 1000,
    }
    metadata.update(kwargs)
    return metadata


def create_mock_response(
    status_code: int = 200, content: str = "", headers: dict | None = None, json_data: dict | None = None
):
    """モックHTTPレスポンスを作成する。

    Args:
        status_code: HTTPステータスコード
        content: レスポンス本文
        headers: HTTPヘッダー
        json_data: JSONレスポンスデータ

    Returns:
        モックレスポンスオブジェクト
    """
    from unittest.mock import Mock

    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.content = content.encode("utf-8") if isinstance(content, str) else content
    mock_response.text = content if isinstance(content, str) else str(content)
    mock_response.headers = headers or {}

    if json_data:
        mock_response.json.return_value = json_data

    return mock_response


def assert_csv_sanitized(csv_data: str) -> bool:
    """CSVデータが適切にサニタイズされているか確認する。

    Args:
        csv_data: 検証するCSVデータ

    Returns:
        サニタイズされている場合True
    """
    dangerous_starts = ["=", "+", "-", "@", "\t", "\r"]

    lines = csv_data.split("\n")
    for line in lines:
        if not line.strip():
            continue
        cells = line.split(",")
        for cell in cells:
            if cell and any(cell.startswith(char) for char in dangerous_starts):
                # 危険な文字で始まる場合、シングルクォートが付いているか確認
                if not cell.startswith("'"):
                    return False

    return True


class TestDataBuilder:
    """テストデータのビルダークラス"""

    def __init__(self):
        self.data = {
            "rows": [],
            "headers": None,
        }

    def with_headers(self, headers: list[str]):
        """ヘッダーを設定する。"""
        self.data["headers"] = headers
        return self

    def add_row(self, row: list):
        """行を追加する。"""
        self.data["rows"].append(row)
        return self

    def add_rows(self, rows: list[list]):
        """複数の行を追加する。"""
        self.data["rows"].extend(rows)
        return self

    def build_csv(self, separator: str = ",") -> str:
        """CSVデータを構築する。"""
        lines = []

        if self.data["headers"]:
            lines.append(separator.join(self.data["headers"]))

        for row in self.data["rows"]:
            lines.append(separator.join(str(cell) for cell in row))

        return "\n".join(lines)

    def build_dict(self) -> list[dict]:
        """辞書のリストを構築する。"""
        if not self.data["headers"]:
            raise ValueError("Headers must be set to build dict")

        result = []
        for row in self.data["rows"]:
            row_dict = dict(zip(self.data["headers"], row, strict=False))
            result.append(row_dict)

        return result
