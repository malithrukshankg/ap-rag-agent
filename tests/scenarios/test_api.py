"""HTTP surface end to end (brief §2, §5 FIN-005; architecture §10)."""

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ap_agent.api.app import create_app
from ap_agent.infra.settings import Settings, load_policy_params, load_registry
from ap_agent.services import ServiceContainer
from tests.conftest import ROOT
from tests.scenarios.conftest import OrchestratorFactory
from tests.scenarios.test_fin_cases import CASES


@pytest.fixture
def app(make_orchestrator: OrchestratorFactory, settings: Settings) -> FastAPI:
    container = ServiceContainer(
        settings=settings,
        registry=load_registry(settings.corpus.registry_path),
        params=load_policy_params(settings.policy.params_path),
        orchestrator=make_orchestrator(),
        cases_dir=ROOT / "fixtures" / "cases",
    )
    return create_app(container=container)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _start(client: TestClient, case_id: str) -> dict[str, Any]:
    f = CASES[case_id]
    body = {
        "case": f.case.model_dump(mode="json"),
        "simulation": {"faults": f.faults, "scripted_model": f.scripted_model},
    }
    resp = client.post("/runs", json=body)
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()
    return data


def _approval(run: dict[str, Any], approver: str = "A-CCM-01") -> dict[str, Any]:
    return {
        "verdict": "approve",
        "approver_id": approver,
        "decision_fingerprint": run["result"]["decision_fingerprint"],
    }


def test_start_get_and_timeline(client: TestClient) -> None:
    run = _start(client, "FIN-001")
    assert run["status"] == "AWAITING_APPROVAL" and run["current_node"] == "REQUEST_APPROVAL"
    got = client.get(f"/runs/{run['run_id']}").json()
    assert got["timeline"] is None and got["audit_events"]
    timeline = client.get(f"/runs/{run['run_id']}", params={"timeline": 1}).json()["timeline"]
    nodes = [t["node"] for t in timeline if t["type"] == "node.entered"]
    assert nodes == ["INTAKE", "RETRIEVE", "GATHER", "RECONCILE", "ASSESS", "DECIDE",
                     "REQUEST_APPROVAL"]  # fmt: skip


def test_invalid_request_is_rejected_at_tb1(client: TestClient) -> None:
    body = {"case": {**CASES["FIN-001"].case.model_dump(mode="json"), "currency": "aud"}}
    assert client.post("/runs", json=body).status_code == 422
    assert client.post("/runs", json={"case": {}, "unexpected": 1}).status_code == 422


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/runs/run-nope").status_code == 404
    body = {"verdict": "approve", "approver_id": "A-CCM-01", "decision_fingerprint": "0" * 64}
    assert client.post("/runs/run-nope/approval", json=body).status_code == 404


def test_failed_check_is_409_and_run_stays_pending(client: TestClient) -> None:
    run = _start(client, "FIN-001")
    resp = client.post(f"/runs/{run['run_id']}/approval", json=_approval(run, "A-INACTIVE"))
    assert resp.status_code == 409
    assert resp.json()["failed_check"] == "approver_unknown_or_inactive"
    assert client.get(f"/runs/{run['run_id']}").json()["status"] == "AWAITING_APPROVAL"


def test_hold_case_completes_without_approval(client: TestClient) -> None:
    run = _start(client, "FIN-003")
    assert run["status"] == "COMPLETED"
    assert run["result"]["recommendation"]["outcome"] == "ESCALATE_CONTROL_REVIEW"


def test_fin_005_concurrent_double_callback(app: FastAPI, client: TestClient) -> None:
    run = _start(client, "FIN-005")
    url, body = f"/runs/{run['run_id']}/approval", _approval(run)

    async def deliver_twice() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return list(await asyncio.gather(ac.post(url, json=body), ac.post(url, json=body)))

    first, second = asyncio.run(deliver_twice())
    assert first.status_code == second.status_code == 200
    assert first.content == second.content  # byte-identical, replay-safe
    assert first.json()["response"]["decision"] == "POST_FOR_PAYMENT"
    orch = app.state.container.orchestrator
    assert len(orch.store.decisions.list_for_case("FIN-005")) == 1
    assert orch.store.db.query_one("SELECT COUNT(*) FROM sim_ledger")[0] == 1


def test_evals_run_and_latest(client: TestClient) -> None:
    assert client.get("/evals/latest").status_code == 404
    report = client.post("/evals").json()
    assert report["passed"] is True, report
    assert [c["case_id"] for c in report["cases"]] == sorted(CASES)
    metrics = {m["name"]: m for m in report["metrics"]}
    assert metrics["superseded_returned"]["value"] == 0
    assert metrics["adversarial_cited_as_authority"]["value"] == 0
    assert metrics["recall_at_5"]["value"] >= 0.8
    assert client.get("/evals/latest").json() == report


def test_openapi_documents_every_operation(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/runs", "/runs/{run_id}", "/runs/{run_id}/approval", "/evals", "/evals/latest"} <= set(
        paths
    )
    assert not any("submit" in p for p in paths)  # the consequential tool is internal
