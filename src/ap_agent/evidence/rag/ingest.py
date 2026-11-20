"""`ap ingest`: parse, chunk and index the corpus (architecture §3.1).

- Front matter is parsed as flat `key: value` lines, not YAML: the corpus is untrusted data,
  and a flat parser keeps every value a plain string ("3.0" stays "3.0").
- One chunk per `##` section; the chunk's section number comes from the heading
  ("## 2. Minimum evidence" → 2). A document with no `##` heading is one chunk, n = 0 (O1).
- chunk_id = {doc_id}§{n}@v{version}; sha256 of the chunk text is stored with it.
- Line endings are normalised to LF before hashing, so a Windows checkout (CRLF) produces
  the same hashes as Linux.
- Idempotent: keyed on a hash of every corpus file plus the registry. Same hash → no-op.
- A file without a `document_id` in its front matter (e.g. the corpus README) is skipped.
  Nothing is skipped by filename or path.
- `status` is stored for display only and is never used as a filter.
"""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from ap_agent.evidence.rag.db import IndexDb
from ap_agent.evidence.rag.registry import PolicyRegistry
from ap_agent.kernel.types import Strict

_HEADING = re.compile(r"^##\s+(?:(\d+)\.?\s*)?(.*)$")


class Chunk(Strict):
    chunk_id: str
    doc_id: str
    version: str
    section: int = Field(ge=0)
    heading: str
    text: str
    text_sha256: str
    effective_date: str | None
    superseded_date: str | None
    jurisdiction: str | None
    status: str | None
    classification: str | None


class IngestReport(Strict):
    corpus_hash: str
    skipped: bool  # True when the index already matched this hash
    documents: int
    chunks: int
    ignored_files: list[str]  # files without a document_id


class IngestError(ValueError):
    pass


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Return (front matter as flat strings, body). No front matter → ({}, text)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip()] = value.strip()
    return meta, text[end + len("\n---\n") :]


def chunk_document(meta: dict[str, str], body: str) -> list[Chunk]:
    doc_id, version = meta.get("document_id"), meta.get("version")
    if not doc_id or not version:
        raise IngestError("front matter needs document_id and version")

    sections: list[tuple[int, str, str]] = []  # (n, heading, text)
    current: tuple[int, str, list[str]] | None = None
    ordinal = 0
    for line in body.splitlines():
        m = _HEADING.match(line)
        if m:
            if current:
                sections.append((current[0], current[1], "\n".join(current[2])))
            ordinal += 1
            n = int(m.group(1)) if m.group(1) else ordinal
            current = (n, m.group(2).strip(), [line])
        elif current:
            current[2].append(line)
    if current:
        sections.append((current[0], current[1], "\n".join(current[2])))

    if not sections:  # O1: headingless document → one chunk
        title = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), doc_id)
        sections = [(0, title, body)]

    chunks: list[Chunk] = []
    seen: set[int] = set()
    for n, heading, raw in sections:
        if n in seen:
            raise IngestError(f"{doc_id}: duplicate section number {n}")
        seen.add(n)
        text = raw.strip()
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}§{n}@v{version}",
                doc_id=doc_id,
                version=version,
                section=n,
                heading=heading,
                text=text,
                text_sha256=_sha256(text),
                effective_date=meta.get("effective_date") or None,
                superseded_date=meta.get("superseded_date") or None,
                jurisdiction=(meta.get("jurisdiction") or "").lower() or None,
                status=meta.get("status") or None,
                classification=meta.get("classification") or None,
            )
        )
    return chunks


def corpus_hash(corpus_dir: Path, registry: PolicyRegistry) -> str:
    h = hashlib.sha256()
    for path in sorted(corpus_dir.glob("*.md")):
        h.update(path.name.encode("utf-8"))
        h.update(_sha256(_read(path)).encode("ascii"))
    h.update(registry.content_hash().encode("ascii"))
    return h.hexdigest()


def ingest(db: IndexDb, corpus_dir: Path, registry: PolicyRegistry) -> IngestReport:
    digest = corpus_hash(corpus_dir, registry)
    chunks: list[Chunk] = []
    ignored: list[str] = []
    doc_ids: set[str] = set()
    for path in sorted(corpus_dir.glob("*.md")):
        meta, body = split_front_matter(_read(path))
        if "document_id" not in meta:
            ignored.append(path.name)
            continue
        doc_ids.add(meta["document_id"])
        chunks.extend(chunk_document(meta, body))

    with db.write():
        row = db.query_one("SELECT value FROM ingest_meta WHERE key = 'corpus_hash'")
        if row is not None and row["value"] == digest:
            return IngestReport(
                corpus_hash=digest,
                skipped=True,
                documents=len(doc_ids),
                chunks=len(chunks),
                ignored_files=ignored,
            )
        db.execute("DELETE FROM chunks")
        db.execute("DELETE FROM chunks_fts")
        db.execute("DELETE FROM policy_registry")
        for c in chunks:
            db.execute(
                "INSERT INTO chunks (chunk_id, doc_id, version, section, heading, text,"
                " text_sha256, effective_date, superseded_date, jurisdiction, status,"
                " classification) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    c.chunk_id,
                    c.doc_id,
                    c.version,
                    c.section,
                    c.heading,
                    c.text,
                    c.text_sha256,
                    c.effective_date,
                    c.superseded_date,
                    c.jurisdiction,
                    c.status,
                    c.classification,
                ),
            )
            db.execute(
                "INSERT INTO chunks_fts (chunk_id, text) VALUES (?, ?)", (c.chunk_id, c.text)
            )
        for doc_id, entry in registry.documents.items():
            db.execute(
                "INSERT INTO policy_registry (doc_id, pinned_version, superseded_by, doc_type)"
                " VALUES (?,?,?,?)",
                (doc_id, entry.pinned_version, entry.superseded_by, entry.doc_type),
            )
        for key, value in (
            ("corpus_hash", digest),
            ("ingested_at", datetime.now(UTC).isoformat(timespec="seconds")),
        ):
            db.execute(
                "INSERT INTO ingest_meta (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    return IngestReport(
        corpus_hash=digest,
        skipped=False,
        documents=len(doc_ids),
        chunks=len(chunks),
        ignored_files=ignored,
    )


def current_corpus_hash(db: IndexDb) -> str | None:
    row = db.query_one("SELECT value FROM ingest_meta WHERE key = 'corpus_hash'")
    return None if row is None else str(row["value"])
