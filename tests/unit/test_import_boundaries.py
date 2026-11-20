"""Enforces the package layering in architecture §0.4 by parsing every import.

Planes may only depend inward. The concrete store (sqlite) is wired in by
orchestration, api and cli; no plane imports them. Architecture §5.2: nothing under
orchestration/ or controls/ reads os.environ, opens YAML, or imports a provider SDK.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "ap_agent"

PORTS = {"infra.persistence.protocols", "infra.persistence.records", "infra.observability"}
CONCRETE_STORES = ("infra.persistence.sqlite",)

# package → ap_agent packages it may import (itself is always allowed)
ALLOWED: dict[str, set[str]] = {
    "kernel": set(),
    "intake": {"kernel"},
    "evidence": {"kernel", "intake"},
    "controls": {"kernel", "intake", "evidence"},
    "assessment": {"kernel", "intake", "evidence", "controls"},
    "decision": {"kernel", "intake", "evidence", "controls", "assessment", *PORTS},
    "infra": {"kernel", "intake", "evidence", "controls", "assessment"},
    "orchestration": {
        "kernel",
        "intake",
        "evidence",
        "controls",
        "assessment",
        "decision",
        "infra",
    },
    "api": {
        "kernel",
        "intake",
        "evidence",
        "controls",
        "assessment",
        "decision",
        "infra",
        "orchestration",
    },
    "cli": {"kernel", "evidence", "infra", "orchestration"},
}

FORBIDDEN_EXTERNAL = {"anthropic", "yaml", "dotenv"}
NO_ENV_PACKAGES = {"orchestration", "controls"}


def _package_of(path: Path) -> str:
    rel = path.relative_to(SRC)
    return rel.parts[0].removesuffix(".py") if len(rel.parts) > 1 else rel.stem


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def _allowed(target: str, allowed: set[str]) -> bool:
    return any(target == a or target.startswith(a + ".") for a in allowed)


SOURCE_FILES = sorted(p for p in SRC.rglob("*.py") if p.parent != SRC or p.stem == "cli")


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_layering(path: Path) -> None:
    pkg = _package_of(path)
    assert pkg in ALLOWED, f"new top-level package {pkg!r}: add it to ALLOWED"
    for name in _imports(path):
        if not name.startswith("ap_agent."):
            continue
        target = name.removeprefix("ap_agent.")
        if target.split(".")[0] == pkg:
            continue
        assert _allowed(target, ALLOWED[pkg]), f"{pkg} must not import {target}"
        if pkg not in {"orchestration", "api", "cli"}:
            assert not target.startswith(CONCRETE_STORES), f"{pkg} imports a concrete store"


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_no_config_or_provider_access_in_core(path: Path) -> None:
    pkg = _package_of(path)
    if pkg not in NO_ENV_PACKAGES:
        return
    source = path.read_text(encoding="utf-8")
    assert "os.environ" not in source and "getenv" not in source
    for name in _imports(path):
        assert name.split(".")[0] not in FORBIDDEN_EXTERNAL, f"{pkg} imports {name}"
