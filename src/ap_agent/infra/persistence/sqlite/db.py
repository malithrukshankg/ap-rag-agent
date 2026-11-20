"""SQLite connection: WAL, single writer, BEGIN IMMEDIATE for every write (architecture §9).

One connection per process, guarded by a re-entrant lock. uvicorn runs with --workers 1, so
this process is the only writer; BEGIN IMMEDIATE takes the write lock up front, so two
callers can never interleave a read-then-write on the same row (the FIN-005 race).
"""

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

Params = Sequence[Any] | Mapping[str, Any]  # positional (?) or named (:name)


def dumps(value: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace, Decimals as strings."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def loads(value: str | None) -> Any:
    return None if value is None else json.loads(value)


class Database:
    def __init__(self, path: str | Path) -> None:
        target = str(path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we issue BEGIN/COMMIT ourselves.
        self.conn = sqlite3.connect(target, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._depth = 0

    def init_schema(self) -> None:
        sql = files("ap_agent.infra.persistence.sqlite").joinpath("schema.sql").read_text("utf-8")
        with self._lock:
            self.conn.executescript(sql)

    @contextmanager
    def write(self) -> Iterator[None]:
        """BEGIN IMMEDIATE ... COMMIT, rollback on any exception. Nested calls join the outer."""
        with self._lock:
            if self._depth > 0:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")
            finally:
                self._depth = 0

    def execute(self, sql: str, params: Params = ()) -> sqlite3.Cursor:
        """A write statement, inside the current transaction or its own."""
        with self.write():
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self.conn.execute(sql, params).fetchone()
            return row

    def journal_mode(self) -> str:
        row = self.query_one("PRAGMA journal_mode")
        return str(row[0]) if row else ""

    def close(self) -> None:
        with self._lock:
            self.conn.close()
