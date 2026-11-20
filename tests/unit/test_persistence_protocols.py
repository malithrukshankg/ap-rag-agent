"""SqliteStore satisfies the Store protocol. mypy --strict checks the assignment below."""

from ap_agent.infra.persistence.protocols import Store
from ap_agent.infra.persistence.sqlite import SqliteStore


def _as_store(s: Store) -> Store:
    return s


def test_sqlite_store_satisfies_protocol(store: SqliteStore) -> None:
    assert _as_store(store) is store
