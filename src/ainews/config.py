from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from .aihot import validate_collection_source
from .models import Source, StrictModel


class Settings(StrictModel):
    discovery_provider: Literal["multi", "aihot"] = "multi"
    timezone: str = "Asia/Singapore"
    report_hour: int = Field(default=9, ge=0, le=23)
    backfill_hours: int = Field(default=72, ge=24, le=168)
    tracking_days: int = Field(default=30, ge=1, le=365)
    mini_model: str = "gpt-5.4-mini"
    major_model: str = "gpt-5.4"
    http_timeout: int = Field(default=20, ge=1, le=120)
    api_timeout: int = Field(default=180, ge=1, le=600)
    attempts: int = Field(default=3, ge=1, le=5)
    batch_size: int = Field(default=12, ge=1, le=25)
    max_candidates: int = Field(default=80, ge=10, le=200)
    max_items: int = Field(default=30, ge=1, le=60)
    max_output_tokens: int = Field(default=10000, ge=1000, le=30000)
    max_search_tool_calls: int = Field(default=8, ge=1, le=20)
    min_source_success_ratio: float = Field(default=.5, ge=0, le=1)
    min_verified_candidates: int = Field(default=5, ge=1)
    max_verification_failure_ratio: float = Field(default=.6, ge=0, le=1)
    required_regions: list[str] = Field(default_factory=lambda: ["north_america", "asia", "europe"])
    max_estimated_cost_usd: float = Field(default=3.0, gt=0)
    web_search_call_usd: float = Field(default=.01, ge=0)
    pricing: dict[str, dict[str, float]] = Field(default_factory=dict)
    watchlist: list[str] = Field(default_factory=list)


def load_config(root: Path):
    settings = Settings.model_validate(yaml.safe_load((root / "config/settings.yaml").read_text("utf-8")))
    sources = [Source.model_validate(s) for s in yaml.safe_load((root / "config/sources.yaml").read_text("utf-8"))["sources"]]
    if len({s.id for s in sources}) != len(sources):
        raise ValueError("Duplicate source IDs")
    for source in sources:
        if settings.discovery_provider == "aihot" and source.enabled and source.kind != "aihot":
            raise ValueError("AIHOT profile may enable only AIHOT discovery sources")
        if source.kind == "aihot":
            validate_collection_source(source)
        if source.kind == "web_search":
            if not source.queries:
                raise ValueError("Web Search source requires queries")
        elif not source.url or not source.allowed_domains:
            raise ValueError("Public source requires URL and allowed_domains")
    return settings, sources
