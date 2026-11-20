"""`ap` command-line entry point. The only command is `ap ingest` (architecture §3.1)."""

import argparse
import json
import sys
from pathlib import Path

from ap_agent.evidence.rag.ingest import IngestError, ingest
from ap_agent.infra.persistence.sqlite import SqliteStore
from ap_agent.infra.settings import DEFAULT_APP_YAML, ConfigError, load_registry, load_settings


def _ingest(config: Path) -> int:
    settings = load_settings(config)
    registry = load_registry(settings.corpus.registry_path)
    store = SqliteStore(settings.storage.sqlite_path)
    try:
        report = ingest(store.db, settings.corpus.path, registry)
    finally:
        store.close()
    print(json.dumps(report.model_dump(mode="json"), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ap")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ingest = sub.add_parser("ingest", help="Chunk and index the corpus into SQLite FTS5")
    p_ingest.add_argument("--config", type=Path, default=DEFAULT_APP_YAML)
    args = parser.parse_args(argv)

    if args.command == "ingest":
        try:
            return _ingest(args.config)
        except (ConfigError, IngestError, FileNotFoundError) as exc:
            print(f"ap ingest: {exc}", file=sys.stderr)
            return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
