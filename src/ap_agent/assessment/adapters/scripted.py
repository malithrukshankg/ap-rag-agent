"""Scripted ModelClient: returns pre-written completions in order (stable tier, no network).

Completions come from the case fixture's `scripted_model` list. A dict entry is serialised
to JSON; a string entry is returned verbatim, so a fixture can script malformed output.
Requests are kept so tests can inspect what the model would have been sent.
"""

import json
from collections.abc import Sequence
from typing import Any

from ap_agent.assessment.client import ModelCallError, ModelRequest, ModelResponse

SCRIPTED_MODEL_ID = "scripted"


class ScriptedModelClient:
    def __init__(self, completions: Sequence[str | dict[str, Any]]) -> None:
        self._completions = [c if isinstance(c, str) else json.dumps(c) for c in completions]
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = len(self.requests) - 1
        if index >= len(self._completions):
            raise ModelCallError("ScriptExhausted")
        text = self._completions[index]
        return ModelResponse(
            text=text,
            model_id=SCRIPTED_MODEL_ID,
            stop_reason="end_turn",
            input_tokens=len(request.system + request.user) // 4,
            output_tokens=len(text) // 4,
        )
