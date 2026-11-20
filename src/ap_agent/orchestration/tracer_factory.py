"""Builds the configured Tracer from Settings. Disabled (the default) means NullTracer and the
langfuse SDK is never imported, so the stable tier and offline runs need no network."""

import threading

from ap_agent.infra.observability.tracing import NULL_TRACER, Tracer
from ap_agent.infra.settings import ConfigError, Settings

# The Langfuse SDK keeps one client per public key, bound to the first TracerProvider it was
# given. A second adapter with the same key would export nothing, so one adapter per
# configuration is shared by every Orchestrator in the process (API app, eval runner).
_CACHE: dict[tuple[str, ...], Tracer] = {}
_CACHE_LOCK = threading.Lock()


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
    key = (public.get_secret_value().strip(), cfg.host, cfg.environment, str(cfg.send_prompts),
           settings.model.prompt_version)  # fmt: skip
    with _CACHE_LOCK:
        if key not in _CACHE:
            # Imported here so the OpenTelemetry/Langfuse stack loads only when tracing is on.
            from ap_agent.infra.observability.langfuse_tracer import LangfuseTracer

            _CACHE[key] = LangfuseTracer(
                public_key=key[0],
                secret_key=secret.get_secret_value().strip(),
                host=cfg.host,
                environment=cfg.environment,
                release=settings.model.prompt_version,
                send_prompts=cfg.send_prompts,
            )
        return _CACHE[key]
