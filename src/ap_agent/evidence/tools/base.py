"""Tool contract (architecture §7, brief §3): explicit input and output schemas, a clear
name, and a typed error taxonomy that the ToolRunner maps to retry behaviour.

Tools are plain callables. Timeouts, retries, budget, persistence and audit events are the
ToolRunner's job (orchestration/tool_runner.py), so every tool gets them uniformly.
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

from ap_agent.kernel.enums import FaultKind

InT = TypeVar("InT", bound=BaseModel)  # invariant: consumed by __call__, exposed as input_model
OutT = TypeVar("OutT", bound=BaseModel, covariant=True)  # only ever produced

# Per case fixture: tool name → one entry consumed per attempt.
FaultProfile = dict[str, list[FaultKind]]


class Tool(Protocol[InT, OutT]):
    @property
    def name(self) -> str: ...
    @property
    def input_model(self) -> type[InT]: ...
    @property
    def output_model(self) -> type[OutT]: ...
    def __call__(self, args: InT) -> OutT: ...


class ToolError(Exception):
    """Base for errors a tool raises on purpose. Messages contain ids only, never data."""


class ToolTimeoutError(ToolError):
    """Attempt exceeded its deadline. Retryable."""


class TransientToolError(ToolError):
    """Temporary backend failure. Retryable."""


class PermanentToolError(ToolError):
    """Not retryable: not found, refused, bad input."""
