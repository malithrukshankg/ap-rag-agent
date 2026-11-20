"""P0 smoke tests: the package imports and the config files agree with architecture.md."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from ap_agent.cli import main

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str) -> dict[str, Any]:
    data = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_budget_matches_architecture() -> None:
    budget = _load("config/app.yaml")["budget"]
    assert budget == {
        "max_steps": 25,
        "max_tool_calls": 25,
        "max_model_calls": 10,
        "max_retries_per_call": 3,
    }


def test_tool_retries_capped_by_budget() -> None:
    cfg = _load("config/app.yaml")
    cap = cfg["budget"]["max_retries_per_call"]
    for name, tool in cfg["tools"].items():
        if isinstance(tool, dict):
            assert tool["max_retries"] <= cap, name


def test_registry_pins_are_strings_and_excludes_adversarial() -> None:
    docs = _load("config/policy_registry.yaml")["documents"]
    assert docs["FIN-POL-003"]["pinned_version"] == "4.0"
    assert docs["FIN-POL-003-OLD"]["pinned_version"] is None  # O2
    assert "ADV-001" not in docs
    assert "ADV-002" not in docs
    for doc_id, entry in docs.items():
        pin = entry["pinned_version"]
        assert pin is None or isinstance(pin, str), doc_id


def test_escalation_precedence_starts_with_fraud() -> None:  # O5
    first = _load("config/app.yaml")["escalation_precedence"][0]
    assert first["source"] == "C6"
    assert first["target"] == "Financial Crime and Controls"


def test_cli_ingest_indexes_then_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("AP_STORAGE__SQLITE_PATH", str(tmp_path / "ap.db"))
    assert main(["ingest"]) == 0
    assert '"skipped": false' in capsys.readouterr().out
    assert main(["ingest"]) == 0
    assert '"skipped": true' in capsys.readouterr().out


def test_cli_ingest_bad_config_exits_1(tmp_path: Path) -> None:
    assert main(["ingest", "--config", str(tmp_path / "missing.yaml")]) == 1
