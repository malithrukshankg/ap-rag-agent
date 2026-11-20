"""Builds the configured Tracer from Settings. Disabled (the default) means NullTracer and the
langfuse SDK is never imported, so the stable tier and offline runs need no network."""

from ap_agent.infra.observability.tracing import NULL_TRACER, Tracer
from ap_agent.infra.settings import ConfigError, Settings


def build_tracer(settings: Settings) -> Tracer:
    cfg = settings.tracing
    if not cfg.enabled:
        return NULL_TRACER
    public, secret = settings.langfuse_public_key, settings.langfuse_secret_key
    if (
        public is None
        or secret is None
        or not public.get_secret_value().strip()
        or not secret.get_secret_value().strip()
    ):
        raise ConfigError(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set when tracing.enabled is true"
        )
    # Imported here so the OpenTelemetry/Langfuse stack loads only when tracing is on.
    from ap_agent.infra.observability.langfuse_tracer import LangfuseTracer

    return LangfuseTracer(
        public_key=public.get_secret_value(),
        secret_key=secret.get_secret_value(),
        host=cfg.host,
        environment=cfg.environment,
        release=settings.model.prompt_version,
        send_prompts=cfg.send_prompts,
    )
