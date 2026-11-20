"""ModelClient port (architecture §5). Two adapters: anthropic (real) and scripted (tests).

The model has no tool access: the port takes text in and returns text out, nothing else.
"""

from typing import Protocol

from pydantic import Field

from ap_agent.kernel.types import Strict


class ModelRequest(Strict):
    system: str  # static; never contains retrieved or case text
    user: str  # case facts, control results and the <untrusted_evidence> block
    max_tokens: int = Field(gt=0)


class ModelResponse(Strict):
    text: str
    model_id: str  # the model that actually served the request (fallbacks may differ)
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    request_id: str | None = None  # auditable per FIN-POL-010 §4


class ModelCallError(Exception):
    """Transport or API failure: no completion was produced. Message holds the class only."""


class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
