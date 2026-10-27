"""METADATA_VERSION 定数行の契約テスト.

.github/workflows/migrate-metadata.yml は src/models/metadata.py をテキストのまま読み、
正規表現で METADATA_VERSION を抜き出して移行の要否を判定する。行の形が変わると
(型注釈の追加など) 抽出が空になり、マージ後に移行ジョブが誤起動して失敗するため、
PR の時点でここで落とす。
"""

import re
from pathlib import Path

import src.models.metadata
from src.models.metadata import METADATA_VERSION

# migrate-metadata.yml の check-version-change / Prepare parameters と同じ正規表現
WORKFLOW_VERSION_PATTERN = re.compile(r"^METADATA_VERSION\s*=\s*[\"']([^\"']*)[\"']", re.MULTILINE)


def test_metadata_version_line_is_extractable_by_migrate_workflow():
    # Arrange
    source = Path(src.models.metadata.__file__).read_text(encoding="utf-8")

    # Act
    match = WORKFLOW_VERSION_PATTERN.search(source)

    # Assert
    assert match is not None
    assert match.group(1) == METADATA_VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+", match.group(1))
