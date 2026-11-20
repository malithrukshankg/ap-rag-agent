"""Fault injection for the simulated backends (architecture §7).

A case fixture declares `faults: {tool_name: [timeout, transient, ok, ...]}`. Each attempt
of that tool consumes one entry; once the list is empty every attempt is `ok`.
"""

from collections import deque

from ap_agent.evidence.tools.base import FaultProfile
from ap_agent.kernel.enums import FaultKind


class FaultInjector:
    def __init__(self, profile: FaultProfile | None = None) -> None:
        self._queues = {tool: deque(kinds) for tool, kinds in (profile or {}).items()}

    def next(self, tool: str) -> FaultKind:
        queue = self._queues.get(tool)
        return queue.popleft() if queue else FaultKind.OK

    def remaining(self, tool: str) -> int:
        return len(self._queues.get(tool, ()))
