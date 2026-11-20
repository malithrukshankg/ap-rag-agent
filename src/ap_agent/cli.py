"""`ap` command-line entry point. The only command is `ap ingest` (architecture §3.1)."""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ap")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="Chunk and index fixtures/corpus into SQLite FTS5")
    args = parser.parse_args(argv)

    if args.command == "ingest":
        # Implemented in P2 (rag/ingest.py).
        print("ap ingest: not implemented yet (P2)", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
