"""Builds the configured ModelClient from Settings (§4: provider config outside orchestration
logic). The graph only ever sees the ModelClient protocol."""

from collections.abc import Sequence
from typing import Any

from ap_agent.assessment.adapters.anthropic import AnthropicModelClient
from ap_agent.assessment.adapters.scripted import ScriptedModelClient
from ap_agent.assessment.client import ModelClient
from ap_agent.infra.settings import Settings


def build_model_client(
    settings: Settings, scripted: Sequence[str | dict[str, Any]] = ()
) -> ModelClient:
    if settings.model.adapter == "scripted":
        return ScriptedModelClient(scripted)
    key = settings.anthropic_api_key
    return AnthropicModelClient(
        model_id=settings.model.id,
        effort=settings.model.effort,
        timeout_s=settings.model.timeout_s,
        api_key=key.get_secret_value() if key is not None else None,
    )
