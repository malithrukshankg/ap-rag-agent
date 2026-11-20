"""resume_after_approval graph (architecture §2.1):

VERIFY_GRANT → SUBMIT(POST_FOR_PAYMENT | REJECTED) → PERSIST → END (COMPLETED)

Runs on the same thread_id as the paused start graph, so LangGraph restores the checkpointed
state; the caller also passes the fields it needs so a lost checkpoint cannot strand a run.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from ap_agent.orchestration.nodes import ResumeNodes
from ap_agent.orchestration.state import RunState


def build_resume_graph(nodes: ResumeNodes) -> StateGraph[Any]:
    g: StateGraph[Any] = StateGraph(RunState)
    g.add_node("VERIFY_GRANT", nodes.wrap("VERIFY_GRANT", nodes.verify_grant))
    g.add_node("SUBMIT", nodes.wrap("SUBMIT", nodes.submit))
    g.add_node("PERSIST", nodes.wrap("PERSIST", nodes.persist))
    g.add_edge(START, "VERIFY_GRANT")
    g.add_edge("VERIFY_GRANT", "SUBMIT")
    g.add_edge("SUBMIT", "PERSIST")
    g.add_edge("PERSIST", END)
    return g
