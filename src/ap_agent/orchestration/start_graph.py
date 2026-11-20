"""start_run graph (architecture §2.1):

INTAKE → RETRIEVE → GATHER → RECONCILE → ASSESS → DECIDE
  DECIDE ─ APPROVE_FOR_POSTING ─→ REQUEST_APPROVAL → END (AWAITING_APPROVAL)
  DECIDE ─ anything else ───────→ SUBMIT(HOLD | REJECTED) → END (COMPLETED)

No interrupt(): the graph ends at REQUEST_APPROVAL and a separate resume graph continues
the same thread after an approval callback.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from ap_agent.orchestration.nodes import StartNodes
from ap_agent.orchestration.state import RunState


def build_start_graph(nodes: StartNodes) -> StateGraph[Any]:
    g: StateGraph[Any] = StateGraph(RunState)
    for name, fn in (
        ("INTAKE", nodes.intake),
        ("RETRIEVE", nodes.retrieve),
        ("GATHER", nodes.gather),
        ("RECONCILE", nodes.reconcile),
        ("ASSESS", nodes.assess),
        ("DECIDE", nodes.decide),
        ("REQUEST_APPROVAL", nodes.request_approval),
        ("SUBMIT", nodes.submit),
    ):
        g.add_node(name, nodes.wrap(name, fn))
    g.add_edge(START, "INTAKE")
    for a, b in (
        ("INTAKE", "RETRIEVE"),
        ("RETRIEVE", "GATHER"),
        ("GATHER", "RECONCILE"),
        ("RECONCILE", "ASSESS"),
        ("ASSESS", "DECIDE"),
    ):
        g.add_edge(a, b)
    g.add_conditional_edges(
        "DECIDE",
        lambda s: "REQUEST_APPROVAL" if s["route"] == "approval" else "SUBMIT",
        {"REQUEST_APPROVAL": "REQUEST_APPROVAL", "SUBMIT": "SUBMIT"},
    )
    g.add_edge("REQUEST_APPROVAL", END)
    g.add_edge("SUBMIT", END)
    return g
