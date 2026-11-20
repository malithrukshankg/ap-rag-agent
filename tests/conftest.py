from collections.abc import Iterator
from pathlib import Path

import pytest

from ap_agent.evidence.rag.ingest import ingest
from ap_agent.evidence.rag.registry import PolicyRegistry
from ap_agent.infra.persistence.sqlite import SqliteStore
from ap_agent.infra.settings import load_registry

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures" / "corpus"
REGISTRY_YAML = ROOT / "config" / "policy_registry.yaml"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStore]:
    """A fresh file-backed store (WAL needs a file, not :memory:)."""
    s = SqliteStore(tmp_path / "ap.db")
    yield s
    s.close()


@pytest.fixture(scope="session")
def registry() -> PolicyRegistry:
    return load_registry(REGISTRY_YAML)


@pytest.fixture(scope="session")
def indexed_store(
    tmp_path_factory: pytest.TempPathFactory, registry: PolicyRegistry
) -> Iterator[SqliteStore]:
    """The real corpus ingested once per session. Read-only: tests must not write to it."""
    s = SqliteStore(tmp_path_factory.mktemp("index") / "ap.db")
    ingest(s.db, CORPUS, registry)
    yield s
    s.close()
