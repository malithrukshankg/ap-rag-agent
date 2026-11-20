"""Tool contract (architecture §7). Concrete input/output schemas live with each tool (P3)."""

from typing import ClassVar, Protocol, TypeVar

from pydantic import BaseModel

from ap_agent.kernel.enums import FaultKind

In = TypeVar("In", bound=BaseModel, contravariant=True)
Out = TypeVar("Out", bound=BaseModel, covariant=True)

# Per case fixture: tool name → one entry consumed per attempt.
FaultProfile = dict[str, list[FaultKind]]


class Tool(Protocol[In, Out]):
    name: ClassVar[str]
    consequential: ClassVar[bool]

    def __call__(self, args: In) -> Out: ...


class ToolTimeoutError(Exception):
    """Attempt exceeded its timeout. Retryable."""


class TransientToolError(Exception):
    """Temporary backend failure. Retryable."""


class PermanentToolError(Exception):
    """Not retryable (bad input, not found, refused)."""
