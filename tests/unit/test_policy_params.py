"""Guards config/policy_params.yaml against drift from the corpus (architecture §4.1).

Every literal must appear verbatim in the indexed chunk of its cited document, section and
version, and that document must be a trusted, registered policy.
"""

import pytest

from ap_agent.infra.persistence.sqlite import SqliteStore
from ap_agent.infra.settings import load_settings
from tests.conftest import ROOT
from tests.unit.control_harness import PARAMS


@pytest.mark.parametrize("key", sorted(PARAMS.params))
def test_literal_exists_in_cited_chunk(indexed_store: SqliteStore, key: str) -> None:
    p = PARAMS[key]
    row = indexed_store.db.query_one(
        "SELECT c.text, r.pinned_version FROM chunks c JOIN policy_registry r USING (doc_id)"
        " WHERE c.doc_id = ? AND c.section = ? AND c.version = ?",
        (p.doc_id, p.section, p.version),
    )
    assert row is not None, f"{key}: no chunk {p.describe()}"
    assert row["pinned_version"] == p.version, f"{key}: cites an unpinned version"
    assert p.literal in row["text"], f"{key}: literal not found in {p.describe()}"


def test_settings_point_at_the_params_file() -> None:
    s = load_settings(ROOT / "config" / "app.yaml")
    assert (ROOT / s.policy.params_path).exists()
