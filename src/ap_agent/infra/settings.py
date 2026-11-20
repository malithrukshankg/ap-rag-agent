"""Typed settings. The ONLY module that reads environment variables or YAML config
(architecture §0.2, §5.2). Everything else receives a `Settings` object by injection.

Precedence, highest first: environment variables (AP_ prefix, `__` for nesting, e.g.
AP_STORAGE__SQLITE_PATH) → .env file → config/app.yaml. Secrets come only from the
environment: ANTHROPIC_API_KEY and AP_PAYMENT_HMAC_KEY.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from ap_agent.controls.params import PolicyParams
from ap_agent.controls.policy import EscalationRule
from ap_agent.evidence.rag.registry import PolicyRegistry

DEFAULT_APP_YAML = Path("config/app.yaml")


class _Cfg(BaseModel):
    """Nested config sections: unknown keys are typos and fail loudly."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelSettings(_Cfg):
    adapter: Literal["scripted", "anthropic"]
    id: str
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    max_tokens: int = Field(gt=0)
    timeout_s: float = Field(gt=0)
    prompt_version: str


class BudgetSettings(_Cfg):
    max_steps: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    max_model_calls: int = Field(gt=0)
    max_retries_per_call: int = Field(ge=0)


class ModelRepairSettings(_Cfg):
    max_repair_attempts: int = Field(ge=0)


class PolicySettings(_Cfg):
    entity: str
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    params_path: Path


class CorpusSettings(_Cfg):
    path: Path
    registry_path: Path


class RetrievalSettings(_Cfg):
    top_k: int = Field(gt=0)


class ToolSettings(_Cfg):
    timeout_s: float = Field(gt=0)
    max_retries: int = Field(ge=0)


class ToolsSettings(_Cfg):
    fixtures_dir: Path
    backoff_s: float = Field(ge=0)
    retrieve_finance_documents: ToolSettings
    get_vendor_record: ToolSettings
    get_purchase_order: ToolSettings
    check_invoice_history: ToolSettings
    get_fx_rate: ToolSettings
    submit_finance_decision: ToolSettings


class StorageSettings(_Cfg):
    sqlite_path: Path


class ApiSettings(_Cfg):
    host: str
    port: int = Field(gt=0, lt=65536)


class LoggingSettings(_Cfg):
    level: str
    path: Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AP_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",  # top-level YAML keys are checked in load_settings instead
        frozen=True,
    )

    model: ModelSettings
    budget: BudgetSettings
    model_repair: ModelRepairSettings
    policy: PolicySettings
    corpus: CorpusSettings
    retrieval: RetrievalSettings
    tools: ToolsSettings
    storage: StorageSettings
    api: ApiSettings
    logging: LoggingSettings
    escalation_precedence: list[EscalationRule]

    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )
    payment_hmac_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AP_PAYMENT_HMAC_KEY")
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # YAML values arrive as init kwargs; environment overrides them.
        return env_settings, dotenv_settings, init_settings


class ConfigError(ValueError):
    pass


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


def load_settings(app_yaml: Path = DEFAULT_APP_YAML) -> Settings:
    data = _read_yaml(app_yaml)
    known = set(Settings.model_fields)
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{app_yaml}: unknown keys {sorted(unknown)}")
    return Settings(**data)


def load_registry(path: Path) -> PolicyRegistry:
    return PolicyRegistry.model_validate(_read_yaml(path))


def load_policy_params(path: Path) -> PolicyParams:
    return PolicyParams.model_validate(_read_yaml(path))
