from collections.abc import Iterator
from pathlib import Path

import pytest

from ap_agent.infra.persistence.sqlite import SqliteStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStore]:
    """A fresh file-backed store (WAL needs a file, not :memory:)."""
    s = SqliteStore(tmp_path / "ap.db")
    yield s
    s.close()
