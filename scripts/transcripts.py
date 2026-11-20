"""Generate the §7 sample transcripts: FIN-001 (approve → post) and FIN-003 (exception).

Drives the real HTTP API in-process (FastAPI TestClient) with the scripted model adapter on a
throwaway SQLite file, so it needs no key, no network and leaves var/ untouched. Output:
docs/transcripts/FIN-00{1,3}.md (readable) and .json (the raw API responses).

    uv run python scripts/transcripts.py

Timestamps, run ids and durations change between runs; outcomes, controls, calculations and
fingerprints do not (the deterministic path, CLAUDE.md resolved ambiguity #10).
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "transcripts"
APPROVER = "A-CCM-01"


def _configure_env(tmp: Path) -> None:
    os.chdir(ROOT)
    os.environ["AP_STORAGE__SQLITE_PATH"] = str(tmp / "ap.db")
    os.environ["AP_LOGGING__PATH"] = str(tmp / "ap.jsonl")
    os.environ["AP_MODEL__ADAPTER"] = "scripted"
    os.environ["AP_TRACING__ENABLED"] = "false"
    os.environ.setdefault("AP_PAYMENT_HMAC_KEY", "transcript-local-key")


def _block(obj: Any) -> str:
    return "```json\n" + json.dumps(obj, indent=2, ensure_ascii=False) + "\n```"


def _md_cell(s: object) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + " …"


def _case_summary(case: dict[str, Any]) -> str:
    """The request as sent, with free text truncated: it is untrusted case data (TB1)."""

    def text(v: Any) -> str:  # Tainted serialises as {"text", "origin"}
        return str(v["text"]) if isinstance(v, dict) else str(v)

    shown = dict(case)
    if isinstance(shown.get("notes"), dict):
        shown["notes"] = text(shown["notes"])
    if shown.get("attachments"):
        shown["attachments"] = [
            {"name": a["name"], "text": _truncate(text(a["text"]), 90) + " (truncated; untrusted)"}
            for a in shown["attachments"]
        ]
    return _block(shown)


def _timeline(run: dict[str, Any]) -> str:
    rows = ["| ts | node | event | outcome | ms | detail |", "|---|---|---|---|---|---|"]
    for t in run["timeline"]:
        rows.append(
            f"| {t['ts'][11:23]} | {t['node'] or ''} | {t['type']} | {t['outcome']} "
            f"| {t['duration_ms'] if t['duration_ms'] is not None else ''} "
            f"| {_md_cell(_truncate(t['detail'], 110))} |"
        )
    return "\n".join(rows)


def _controls(result: dict[str, Any]) -> str:
    rows = [
        "| control | status | floor | category | observed | citations |",
        "|---|---|---|---|---|---|",
    ]
    for c in result["policy_findings"]:
        cites = ", ".join(f"{x['chunk_id']} ({x['trust']})" for x in c["citations"])
        rows.append(
            f"| {c['control_id']} | {c['status']} | {c['outcome_floor']} "
            f"| {c['exception_category'] or ''} | {_md_cell(_truncate(c['observed'] or '', 90))} "
            f"| {_md_cell(cites)} |"
        )
    return "\n".join(rows)


def _calculations(result: dict[str, Any]) -> str:
    rows = ["| id | formula | result |", "|---|---|---|"]
    for c in result["calculations"]:
        rows.append(f"| {c['id']} | `{_md_cell(c['formula'])}` | {c['result']} {c['currency']} |")
    return "\n".join(rows)


def _recommendation(result: dict[str, Any]) -> str:
    r = result["recommendation"]
    lines = [
        f"- **Outcome:** `{r['outcome']}`",
        f"- **Exception category:** {r['exception_category'] or 'none'}",
        f"- **Escalation target:** {r['escalation_target'] or 'none'}",
        f"- **Next action:** {r['next_action']}",
        f"- **Confidence:** {r['confidence']['level']} ({'; '.join(r['confidence']['basis'])})",
    ]
    lines += [f"- **Reason:** {x}" for x in r["reasons"]]
    pv = ", ".join(f"{k} v{v}" for k, v in sorted(result["applied_policy_versions"].items()))
    lines.append(f"- **Applied policy versions:** {pv}")
    return "\n".join(lines)


def _list(items: list[Any], fmt: Any) -> str:
    return "\n".join(f"- {fmt(x)}" for x in items) if items else "- (none)"


def _result_sections(result: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            "### Recommendation\n" + _recommendation(result),
            "### Policy findings (controls C1 to C6, computed in code)\n" + _controls(result),
            "### Calculations (Decimal, ROUND_HALF_UP; never model-produced)\n"
            + _calculations(result),
            "### Inferences\n"
            + _list(result["inferences"], lambda i: f"[{i['origin']}] {i['text']}"),
            "### Unknowns\n"
            + _list(result["unknowns"], lambda u: f"**{u['what']}**: {u['why']} → {u['impact']}"),
            "### Actions taken\n"
            + _list(
                result["actions_taken"],
                lambda a: (
                    f"`{a['action']}`: {a['detail']}"
                    + (f" (ref `{a['reference']}`)" if a["reference"] else "")
                ),
            ),
        ]
    )


def _prompt_excerpt(prompt: str, marker: str, width: int = 700) -> str:
    start = prompt.find(marker)
    if start < 0:
        return "(marker not found)"
    return "```text\n" + _truncate(prompt[start:], width) + "\n```"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ap-transcripts-"))
    _configure_env(tmp)
    sys.path.insert(0, str(ROOT / "src"))

    from fastapi.testclient import TestClient

    from ap_agent.api.app import create_app
    from ap_agent.orchestration.evals.cases import load_case
    from ap_agent.services.container import build_container

    container = build_container()
    OUT.mkdir(parents=True, exist_ok=True)

    def start(client: TestClient, case_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        f = load_case(ROOT / "fixtures" / "cases" / f"{case_id}.json")
        body = {
            "case": f.case.model_dump(mode="json"),
            "simulation": {"faults": f.faults, "scripted_model": f.scripted_model},
        }
        resp = client.post("/runs", json=body)
        resp.raise_for_status()
        return body["case"], resp.json()

    def get(client: TestClient, run_id: str) -> dict[str, Any]:
        resp = client.get(f"/runs/{run_id}", params={"timeline": 1})
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    def prompt_for(run_id: str) -> str:
        row = container.orchestrator.store.db.query_one(
            "SELECT prompt_redacted FROM model_calls WHERE run_id = ? ORDER BY attempt LIMIT 1",
            (run_id,),
        )
        return str(row[0]) if row else ""

    def retrieval_table(run_id: str) -> tuple[str, list[str]]:
        """Persisted retrieval sets. Trust is derived as the index does: registry membership."""
        db = container.orchestrator.store.db
        registered = {str(r[0]) for r in db.query("SELECT doc_id FROM policy_registry")}
        rows = ["| query | top chunks (score, trust) |", "|---|---|"]
        untrusted: set[str] = set()
        for r in db.query(
            "SELECT query_label, chunk_ids, scores FROM retrievals WHERE run_id = ? ORDER BY ts",
            (run_id,),
        ):
            hits = []
            for cid, score in zip(json.loads(r[1]), json.loads(r[2]), strict=True):
                trust = "trusted" if cid.split("§")[0] in registered else "untrusted"
                if trust == "untrusted":
                    untrusted.add(cid)
                    hits.append(f"**{cid}** ({float(score):.2f}, untrusted)")
                else:
                    hits.append(f"{cid} ({float(score):.2f})")
            rows.append(f"| {r[0]} | {_md_cell(', '.join(hits))} |")
        return "\n".join(rows), sorted(untrusted)

    with TestClient(create_app(container=container)) as client:
        # ---- FIN-001: approve → post, with the callback delivered twice -------------------
        case1, started1 = start(client, "FIN-001")
        run_id = started1["run_id"]
        body = {
            "verdict": "approve",
            "approver_id": APPROVER,
            "decision_fingerprint": started1["result"]["decision_fingerprint"],
        }
        first = client.post(f"/runs/{run_id}/approval", json=body)
        second = client.post(f"/runs/{run_id}/approval", json=body)
        final1 = get(client, run_id)
        ledger = container.orchestrator.store.db.query_one("SELECT COUNT(*) FROM sim_ledger")
        ledger_rows = int(ledger[0]) if ledger else -1

        md1 = f"""# Transcript: FIN-001 valid three-way match (approve → post)

Generated by `uv run python scripts/transcripts.py` against the real HTTP API in-process, with
the **scripted** model adapter (stable tier, no key). The vendor, PO, history and ledger are
**SIMULATED** fixtures; retrieval is the real SQLite FTS5 index. Run ids and timestamps vary
between runs; outcomes, controls, calculations and fingerprints do not.

## 1. `POST /runs` → {started1["status"]}

Request (case only; the `simulation` block carries the scripted completion):

{_case_summary(case1)}

The run stops at `{started1["current_node"]}` with status **{started1["status"]}**. No decision
has been written: posting is consequential, so the graph created an approval request and ended
(§2.5).

{_result_sections(started1["result"])}

Decision fingerprint the approver must echo back (FIN-POL-003 §5):
`{started1["result"]["decision_fingerprint"]}`

## 2. `POST /runs/{{run_id}}/approval`, delivered twice (FIN-005 behaviour)

Request body:

{_block(body)}

First delivery → HTTP {first.status_code}:

{_block(first.json())}

Second delivery → HTTP {second.status_code}. Byte-identical to the first:
**{first.content == second.content}**. Simulated ledger rows after both deliveries:
**{ledger_rows}**.

## 3. `GET /runs/{{run_id}}?timeline=1` → {final1["status"]}

Budget spent: `{json.dumps(final1["budget"])}`

{_timeline(final1)}

### Final actions taken
{
            _list(
                final1["result"]["actions_taken"],
                lambda a: (
                    f"`{a['action']}`: {a['detail']}"
                    + (f" (ref `{a['reference']}`)" if a["reference"] else "")
                ),
            )
        }

Approval summary: `{json.dumps(final1["result"]["approval"])}`
"""
        (OUT / "FIN-001.md").write_text(md1, encoding="utf-8")
        (OUT / "FIN-001.json").write_text(
            json.dumps(
                {
                    "start": started1,
                    "approval_1": first.json(),
                    "approval_2": second.json(),
                    "final": final1,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # ---- FIN-003: poisoned attachment → escalate, hold, no approval ------------------
        case3, started3 = start(client, "FIN-003")
        final3 = get(client, started3["run_id"])
        result3 = final3["result"]
        retrieval_md, untrusted_hits = retrieval_table(started3["run_id"])
        untrusted_list = ", ".join(f"`{c}`" for c in untrusted_hits) or "(none)"
        prompt3 = prompt_for(started3["run_id"])
        approval_events = [e for e in final3["audit_events"] if e["type"].startswith("approval.")]

        md3 = f"""# Transcript: FIN-003 poisoned document (exception flow)

Generated by `uv run python scripts/transcripts.py` (scripted model adapter; SIMULATED
fixtures; real FTS5 retrieval). The supplier attachment tells the reader to ignore policy,
skip checks, avoid a human approver and pay a new account. It is **untrusted case data**: it is
retrieved and reasoned about, never obeyed.

## 1. `POST /runs` → {final3["status"]}

Request (attachment text truncated here; full fixture in `fixtures/cases/FIN-003.json`):

{_case_summary(case3)}

The run ends **{final3["status"]}** at `{final3["current_node"]}` without an approval request
(approval events in the audit log: {len(approval_events)}). The decision written is a `HOLD`
through `submit_finance_decision`, which needs no approval (resolved ambiguity #1).

{_result_sections(result3)}

## 2. How the injection was contained

- **Retrieval (TB2):** untrusted chunks returned on relevance and flagged by registry
  provenance, not filtered by name: {untrusted_list}. Full sets below.
- **Controls:** C3 compared the attachment's claimed account (last4 only) with the vendor master
  in code; C6 counted the attachment's fraud indicators deterministically. Neither reads
  instructions.
- **Model (TB3):** the model saw the attachment only inside `<untrusted_evidence>`, escaped and
  labelled `trust="untrusted"`. It has no tools, and its proposal can only raise the outcome
  floor. Its risk indicators passed citation verification because they quote the case's own
  text.
- **Gate (TB4):** the outcome is not `APPROVE_FOR_POSTING`, so no approval request exists, and
  `POST_FOR_PAYMENT` without a matching grant would be refused inside the tool anyway.

Retrieval sets as persisted in `retrievals` (BM25 score; untrusted chunks in bold). ADV-001 tops
the CASE arm on relevance and also ranks in four control arms; it is never cited as authority:

{retrieval_md}

Excerpt of the stored redacted prompt (`model_calls.prompt_redacted`), around the attachment:

{_prompt_excerpt(prompt3, '<source id="case:notes"')}

## 3. `GET /runs/{{run_id}}?timeline=1`

Budget spent: `{json.dumps(final3["budget"])}`

{_timeline(final3)}
"""
        (OUT / "FIN-003.md").write_text(md3, encoding="utf-8")
        (OUT / "FIN-003.json").write_text(
            json.dumps({"start": started3, "final": final3}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    container.close()
    print(f"wrote {OUT / 'FIN-001.md'} and {OUT / 'FIN-003.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
