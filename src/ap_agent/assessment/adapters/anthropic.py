"""Anthropic ModelClient (REAL, needs network and ANTHROPIC_API_KEY; live tier only).

The only module in the codebase that imports the provider SDK. Model id, effort, timeout
and the key are passed in by the wiring code from Settings (§4 "provider configuration
outside the orchestration code").

- Plain text in, plain text out. JSON validation and repair happen in assess.py, so a
  malformed completion is handled the same way whichever adapter produced it.
- SDK retries are disabled (max_retries=0): every attempt goes through our own bounded loop,
  so every attempt is charged to the run budget and recorded.
- Server-side refusal fallback is enabled (`fallbacks="default"`); the model that actually
  served the request is recorded from the response.
"""

from typing import Literal

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam, BetaTextBlock

from ap_agent.assessment.client import ModelCallError, ModelRequest, ModelResponse

FALLBACK_BETA = "server-side-fallback-2026-07-01"
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class AnthropicModelClient:
    def __init__(
        self,
        *,
        model_id: str,
        effort: Effort,
        timeout_s: float,
        api_key: str | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._model_id = model_id
        self._effort = effort
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout_s, max_retries=0
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        messages: list[BetaMessageParam] = [{"role": "user", "content": request.user}]
        output_config: BetaOutputConfigParam = {"effort": self._effort}
        try:
            message = self._client.beta.messages.create(
                model=self._model_id,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=messages,
                output_config=output_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
            # APITimeoutError is an APIConnectionError. Record the class, never the body.
            raise ModelCallError(type(exc).__name__) from exc

        text = "".join(b.text for b in message.content if isinstance(b, BetaTextBlock))
        return ModelResponse(
            text=text,
            model_id=message.model,
            stop_reason=message.stop_reason,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            request_id=getattr(message, "_request_id", None),
        )
