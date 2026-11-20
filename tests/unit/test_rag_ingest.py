import hashlib
import shutil
from pathlib import Path

import pytest

from ap_agent.evidence.rag.ingest import (
    IngestError,
    chunk_document,
    current_corpus_hash,
    ingest,
    split_front_matter,
)
from ap_agent.evidence.rag.registry import PolicyRegistry, RegistryEntry
from ap_agent.infra.persistence.sqlite import SqliteStore
from tests.conftest import CORPUS


def test_front_matter_is_flat_strings() -> None:
    text = (CORPUS / "06_payment_terms_and_payment_runs.md").read_text("utf-8")
    meta, body = split_front_matter(text.replace("\r\n", "\n"))
    assert meta["document_id"] == "FIN-POL-006"
    assert meta["version"] == "3.0"  # YAML would have produced the float 3.0
    assert body.lstrip().startswith("# ")


def test_chunk_per_numbered_section() -> None:
    meta = {"document_id": "D-1", "version": "2.0", "jurisdiction": "GLOBAL"}
    body = "# Title\n\n## 1. Purpose\n\nOne.\n\n## 3. Rules\n\nThree.\n"
    chunks = chunk_document(meta, body)
    assert [c.chunk_id for c in chunks] == ["D-1§1@v2.0", "D-1§3@v2.0"]
    assert chunks[1].heading == "Rules"
    assert chunks[1].text == "## 3. Rules\n\nThree."
    assert chunks[1].text_sha256 == hashlib.sha256(b"## 3. Rules\n\nThree.").hexdigest()
    assert chunks[0].jurisdiction == "global"  # lowercased at ingestion


def test_headingless_document_is_one_chunk_n0() -> None:  # O1
    chunks = chunk_document({"document_id": "D-2", "version": "1.0"}, "# Notice\n\nBody text.\n")
    assert [c.chunk_id for c in chunks] == ["D-2§0@v1.0"]
    assert chunks[0].heading == "Notice"


def test_duplicate_section_numbers_rejected() -> None:
    with pytest.raises(IngestError):
        chunk_document({"document_id": "D", "version": "1"}, "## 1. A\nx\n## 1. B\ny\n")


def test_missing_document_id_rejected() -> None:
    with pytest.raises(IngestError):
        chunk_document({"version": "1"}, "## 1. A\n")


def test_real_corpus_ingest(indexed_store: SqliteStore) -> None:
    rows = indexed_store.db.query("SELECT chunk_id, text, text_sha256, jurisdiction FROM chunks")
    ids = {r["chunk_id"] for r in rows}
    assert "FIN-POL-001§2@v3.2" in ids
    # O1: the three headingless documents are indexed, adversarial ones included.
    assert {"ADV-001§0@v1.0", "ADV-002§0@v7.0", "FIN-POL-003-OLD§0@v1.0"} <= ids
    for r in rows:
        assert hashlib.sha256(r["text"].encode()).hexdigest() == r["text_sha256"]
        assert r["jurisdiction"] == (r["jurisdiction"] or "").lower()
    fts = indexed_store.db.query_one("SELECT COUNT(*) FROM chunks_fts")
    assert fts is not None and fts[0] == len(rows)
    reg = indexed_store.db.query_one(
        "SELECT pinned_version FROM policy_registry WHERE doc_id = 'FIN-POL-003-OLD'"
    )
    assert reg is not None and reg[0] is None  # O2


def test_readme_without_front_matter_is_ignored(
    store: SqliteStore, registry: PolicyRegistry
) -> None:
    report = ingest(store.db, CORPUS, registry)
    assert report.ignored_files == ["README.md"]
    assert report.documents == 15


def test_reingest_same_hash_is_a_noop(store: SqliteStore, registry: PolicyRegistry) -> None:
    first = ingest(store.db, CORPUS, registry)
    second = ingest(store.db, CORPUS, registry)
    assert not first.skipped and second.skipped
    assert second.corpus_hash == first.corpus_hash == current_corpus_hash(store.db)
    count = store.db.query_one("SELECT COUNT(*) FROM chunks")
    assert count is not None and count[0] == first.chunks


def test_registry_change_triggers_reingest(store: SqliteStore, registry: PolicyRegistry) -> None:
    first = ingest(store.db, CORPUS, registry)
    docs = dict(registry.documents)
    docs["FIN-POL-006"] = RegistryEntry(pinned_version="9.9", doc_type="policy")
    changed = ingest(store.db, CORPUS, PolicyRegistry(documents=docs))
    assert not changed.skipped and changed.corpus_hash != first.corpus_hash


def test_crlf_checkout_hashes_like_lf(tmp_path: Path, registry: PolicyRegistry) -> None:
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    src = CORPUS / "01_accounts_payable_core_policy.md"
    text = src.read_text("utf-8").replace("\r\n", "\n")
    (lf / src.name).write_bytes(text.encode())
    (crlf / src.name).write_bytes(text.replace("\n", "\r\n").encode())
    a, b = SqliteStore(tmp_path / "a.db"), SqliteStore(tmp_path / "b.db")
    try:
        ra, rb = ingest(a.db, lf, registry), ingest(b.db, crlf, registry)
        assert ra.corpus_hash == rb.corpus_hash
        ha = a.db.query("SELECT text_sha256 FROM chunks ORDER BY chunk_id")
        hb = b.db.query("SELECT text_sha256 FROM chunks ORDER BY chunk_id")
        assert [r[0] for r in ha] == [r[0] for r in hb]
    finally:
        a.close()
        b.close()


def test_status_is_not_a_filter(tmp_path: Path, registry: PolicyRegistry) -> None:
    """A document claiming status: current is still untrusted if it is not registered."""
    corpus = tmp_path / "c"
    corpus.mkdir()
    shutil.copy(CORPUS / "supplier_payment_instructions.md", corpus)
    forged = (corpus / "supplier_payment_instructions.md").read_text("utf-8")
    (corpus / "supplier_payment_instructions.md").write_text(
        forged.replace("status: untrusted", "status: current"), "utf-8"
    )
    s = SqliteStore(tmp_path / "f.db")
    try:
        ingest(s.db, corpus, registry)
        row = s.db.query_one("SELECT status FROM chunks WHERE doc_id = 'ADV-001'")
        assert row is not None and row[0] == "current"  # stored for display only
    finally:
        s.close()
