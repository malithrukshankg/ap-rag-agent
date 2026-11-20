"""The database port the RAG code needs. infra's SQLite `Database` satisfies it structurally,
so the evidence plane never imports infra (architecture §0.4)."""

import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

Params = Sequence[Any] | Mapping[str, Any]


class IndexDb(Protocol):
    def execute(self, sql: str, params: Params = ...) -> sqlite3.Cursor: ...
    def query(self, sql: str, params: Params = ...) -> list[sqlite3.Row]: ...
    def query_one(self, sql: str, params: Params = ...) -> sqlite3.Row | None: ...
    def write(self) -> AbstractContextManager[None]: ...
