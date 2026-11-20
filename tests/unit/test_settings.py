from pathlib import Path

import pytest

from ap_agent.infra.settings import ConfigError, load_registry, load_settings
from tests.conftest import REGISTRY_YAML, ROOT

APP_YAML = ROOT / "config" / "app.yaml"


def test_loads_app_yaml() -> None:
    s = load_settings(APP_YAML)
    assert s.model.adapter == "scripted"
    assert s.budget.max_tool_calls == 25
    assert s.tools.get_purchase_order.max_retries == 3
    assert s.escalation_precedence[0].target == "Financial Crime and Controls"


def test_env_overrides_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AP_STORAGE__SQLITE_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("AP_PAYMENT_HMAC_KEY", "s3cr3t-hmac-value")
    s = load_settings(APP_YAML)
    assert s.storage.sqlite_path == tmp_path / "x.db"
    assert s.payment_hmac_key is not None
    assert s.payment_hmac_key.get_secret_value() == "s3cr3t-hmac-value"
    assert "s3cr3t-hmac-value" not in repr(s)  # secrets never printed


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "app.yaml"
    bad.write_text(APP_YAML.read_text("utf-8") + "\nbudgett: {}\n", "utf-8")
    with pytest.raises(ConfigError, match="budgett"):
        load_settings(bad)


def test_registry_loads_with_string_pins() -> None:
    reg = load_registry(REGISTRY_YAML)
    assert reg.documents["FIN-POL-006"].pinned_version == "3.0"
    assert reg.documents["FIN-POL-003-OLD"].pinned_version is None
