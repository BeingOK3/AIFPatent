from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "ai4patent.json"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppSettings(StrictModel):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)


class FeatureSettings(StrictModel):
    idea: bool
    deepdive: bool
    pct: bool
    value: bool
    cfp: bool

    @model_validator(mode="after")
    def only_idea_is_available(self) -> "FeatureSettings":
        if not self.idea or self.deepdive or self.pct or self.value or self.cfp:
            raise ValueError("only the IDEA feature may be enabled during the rebuild")
        return self


class ModelSettings(StrictModel):
    provider: str = Field(min_length=1)
    default: str = Field(min_length=1)
    base_url: HttpUrl
    api_key_env: str = Field(min_length=1)
    auth_file: Path
    auth_provider: str = Field(min_length=1)
    timeout_seconds: int = Field(ge=1)
    max_output_tokens: int = Field(ge=256)
    structured_output_retries: int = Field(ge=0, le=10)
    temperature: float = Field(ge=0, le=2)


class CacheSettings(StrictModel):
    max_bytes: int = Field(gt=0)
    low_watermark_bytes: int = Field(ge=0)
    eviction_policy: Literal["fifo"]
    cleanup_after_write: bool

    @model_validator(mode="after")
    def low_watermark_must_fit(self) -> "CacheSettings":
        if self.low_watermark_bytes > self.max_bytes:
            raise ValueError("cache low watermark must not exceed max_bytes")
        return self


class HistorySettings(StrictModel):
    auto_delete: Literal[False]
    manual_delete_enabled: Literal[True]


class StorageSettings(StrictModel):
    database: Path
    runs_dir: Path
    uploads_dir: Path
    cache_dir: Path
    document_store_dir: Path
    cache: CacheSettings
    history: HistorySettings


class WorkflowSettings(StrictModel):
    step_timeout_seconds: int = Field(ge=1)
    max_step_attempts: int = Field(ge=1)
    document_agent_concurrency: int = Field(ge=1)
    inventive_route_concurrency: int = Field(ge=1)
    resume_incomplete_runs_on_startup: bool


class ProviderSettings(StrictModel):
    enabled: bool
    timeout_seconds: int = Field(ge=1)
    max_attempts: int = Field(ge=1)


class ExaSettings(ProviderSettings):
    endpoint: HttpUrl
    search_tool: str = Field(min_length=1)
    fetch_tool: str = Field(min_length=1)
    fetch_max_characters: int = Field(ge=10_000, le=500_000)


class GooglePatentsSettings(ProviderSettings):
    base_url: HttpUrl
    min_request_interval_seconds: float = Field(ge=0)
    user_agent: str = Field(min_length=1)
    trust_environment_proxy: bool
    fallback_to_direct: bool


class LocalCacheProviderSettings(StrictModel):
    enabled: bool


class SearchProviders(StrictModel):
    exa_mcp: ExaSettings
    google_patents_local: GooglePatentsSettings
    local_cache: LocalCacheProviderSettings


class SearchMode(StrictModel):
    candidate_max: int = Field(ge=10)
    deep_review_min: int = Field(ge=10)
    deep_review_max: int = Field(ge=10)

    @model_validator(mode="after")
    def validate_limits(self) -> "SearchMode":
        if self.deep_review_max < self.deep_review_min:
            raise ValueError("deep_review_max must be >= deep_review_min")
        if self.candidate_max < self.deep_review_max:
            raise ValueError("candidate_max must be >= deep_review_max")
        return self


class SearchModes(StrictModel):
    quick: SearchMode
    standard: SearchMode
    deep: SearchMode


class SaturationSettings(StrictModel):
    consecutive_rounds: int = Field(ge=1)
    max_new_high_relevance_families: int = Field(ge=0)


class SearchSettings(StrictModel):
    providers: SearchProviders
    default_mode: Literal["quick", "standard", "deep"]
    modes: SearchModes
    saturation: SaturationSettings

    def mode(self, name: str | None = None) -> SearchMode:
        return getattr(self.modes, name or self.default_mode)


class OutputSettings(StrictModel):
    always_save_markdown: Literal[True]
    always_save_json: Literal[True]
    generate_docx_by_default: bool
    generate_xlsx_by_default: bool


class LoggingSettings(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    log_full_user_input: bool
    log_full_model_output: bool


class AppConfig(StrictModel):
    schema_path: str | None = Field(default=None, alias="$schema")
    app: AppSettings
    features: FeatureSettings
    model: ModelSettings
    storage: StorageSettings
    workflow: WorkflowSettings
    search: SearchSettings
    outputs: OutputSettings
    logging: LoggingSettings
    source_path: Path = Field(exclude=True)

    def snapshot(self) -> dict:
        data = self.model_dump(mode="json", by_alias=True, exclude={"source_path"})
        data.pop("$schema", None)
        return data


class ConfigError(RuntimeError):
    """Raised when the application configuration cannot be trusted."""


def _absolute(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else (root / path).resolve()


def load_config(path: str | Path | None = None) -> AppConfig:
    selected = Path(path or os.environ.get("AI4PATENT_CONFIG", DEFAULT_CONFIG_PATH)).resolve()
    try:
        raw = json.loads(selected.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file does not exist: {selected}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"configuration is not valid JSON: {selected}: {exc}") from exc

    try:
        config = AppConfig.model_validate({**raw, "source_path": selected})
    except ValidationError as exc:
        raise ConfigError(f"configuration validation failed: {exc}") from exc

    root = PROJECT_ROOT
    storage = config.storage.model_copy(
        update={
            "database": _absolute(config.storage.database, root),
            "runs_dir": _absolute(config.storage.runs_dir, root),
            "uploads_dir": _absolute(config.storage.uploads_dir, root),
            "cache_dir": _absolute(config.storage.cache_dir, root),
            "document_store_dir": _absolute(config.storage.document_store_dir, root),
        }
    )
    model = config.model.model_copy(update={"auth_file": _absolute(config.model.auth_file, root)})
    return config.model_copy(update={"storage": storage, "model": model})
