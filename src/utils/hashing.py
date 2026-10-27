"""File hashing shared by the processor and the coverage check."""

import hashlib
from pathlib import Path

_CHUNK_SIZE = 65536


def sha256_file(file_path: Path) -> str:
    """Return the sha256 hex digest of the file's bytes as stored, without decoding or newline translation."""
    sha256 = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            sha256.update(chunk)
    return sha256.hexdigest()
