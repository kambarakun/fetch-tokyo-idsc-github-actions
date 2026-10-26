"""Tests for src/utils/hashing.py."""

import hashlib
from pathlib import Path

from src.utils.hashing import sha256_file


def test_sha256_file_hashes_raw_bytes_across_chunks(tmp_path: Path) -> None:
    # Arrange: Shift_JIS text with CRLF, longer than one read chunk, so decoding or newline
    # translation would change the digest
    content = "疾病名,報告数\r\n".encode("shift_jis") * 10_000
    file_path = tmp_path / "raw.csv"
    file_path.write_bytes(content)

    # Act
    digest = sha256_file(file_path)

    # Assert
    assert len(content) > 65536
    assert digest == hashlib.sha256(content).hexdigest()


def test_sha256_file_of_empty_file_is_empty_digest(tmp_path: Path) -> None:
    # Arrange
    file_path = tmp_path / "empty.csv"
    file_path.write_bytes(b"")

    # Act
    digest = sha256_file(file_path)

    # Assert
    assert digest == hashlib.sha256(b"").hexdigest()
