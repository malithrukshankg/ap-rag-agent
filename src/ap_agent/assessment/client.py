"""ModelClient port (architecture §5). Adapters (anthropic, scripted) arrive in P5.

The model has no tool access: the port takes text in and returns text out, nothing else.
"""

from typing import Protocol

from pydantic import Field

from ap_agent.kernel.types import Strict


class ModelRequest(Strict):
    system: str  # static; never contains retrieved or case text
    user: str  # carries the <untrusted_evidence> block
    max_tokens: int = Field(gt=0)


class ModelResponse(Strict):
    text: str
    model_id: str
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
