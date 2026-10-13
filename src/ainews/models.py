from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

Category = Literal["models", "agents", "opensource", "business", "applications", "infrastructure"]
Priority = Literal["P0", "P1", "P2", "P3"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(StrictModel):
    id: str
    name: str = ""
    kind: Literal["rss", "api", "page", "web_search"]
    url: str = ""
    enabled: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    region: str = "global"
    categories: list[Category] = Field(default_factory=lambda: ["models"])
    timezone: str | None = None
    max_items: int = Field(default=20, ge=1, le=100)
    items_path: str = ""
    fields: dict[str, str] = Field(default_factory=dict)
    item_selector: str = "article"
    title_selector: str = "h2"
    link_selector: str = "a"
    date_selector: str = "time"
    excerpt_selector: str = "p"
    respect_robots: bool = True
    queries: list[str] = Field(default_factory=list)


class Candidate(StrictModel):
    id: str
    source_id: str
    title: str = Field(min_length=1, max_length=500)
    url: str
    excerpt: str = Field(max_length=1800)
    published_at: AwareDatetime | None
    time_precision: Literal["exact", "date", "unknown"]
    collected_at: AwareDatetime
    region: str
    categories: list[Category] = Field(default_factory=list)
    publication_basis: str = "source_metadata"
    verified: bool = False
    verification_note: str = "not_checked"


class SourceStatus(StrictModel):
    source_id: str
    name: str
    kind: str
    region: str
    categories: list[Category]
    status: Literal["ok", "failed", "disabled", "skipped"]
    fetched: int = 0
    eligible: int = 0
    rejected: int = 0
    error: str | None = None
    checked_at: AwareDatetime


class Scores(StrictModel):
    impact: int = Field(ge=0, le=5)
    novelty: int = Field(ge=0, le=5)
    relevance: int = Field(ge=0, le=5)
    confidence: int = Field(ge=0, le=5)
    follow_up: int = Field(ge=0, le=5)

    @property
    def weighted(self):
        return round(self.impact * .30 + self.novelty * .25 + self.relevance * .20
                     + self.confidence * .15 + self.follow_up * .10, 2)


class DraftItem(StrictModel):
    event_key: str = Field(min_length=3, max_length=180)
    milestone: str = Field(min_length=3, max_length=180)
    title: str = Field(min_length=3, max_length=180)
    category: Category
    priority: Priority
    score: Scores
    confidence: Literal["high", "medium", "low"]
    occurred_at: AwareDatetime | None
    is_followup: bool
    previous_event_id: str | None
    new_information: str = Field(max_length=1000)
    summary: str = Field(min_length=10, max_length=1400)
    significance: str = Field(max_length=1000)
    analysis: str = Field(max_length=1400)
    followup_questions: list[str]
    candidate_ids: list[str] = Field(min_length=1, max_length=6)
    tags: list[str] = Field(max_length=8)
    fact_status: Literal["FACT", "UNVERIFIED"]
    repository_url: str | None
    technical_highlights: str | None
    target_users: str | None
    maturity_limitations: str | None

    @field_validator("tags", "followup_questions")
    @classmethod
    def limit_strings(cls, values):
        if any(len(v) > 500 for v in values):
            raise ValueError("String too long")
        return values


class EditorialBatch(StrictModel):
    news_items: list[DraftItem] = Field(max_length=40)
    rejected_candidate_ids: list[str]


class Review(StrictModel):
    approved: bool
    supported_candidate_ids: list[str]
    issues: list[str]
    independent_source_count: int = Field(ge=0, le=10)
    occurred_at_supported: bool
    new_information_supported: bool


class Citation(StrictModel):
    candidate_id: str
    url: str
    name: str
    source_type: str
    verification_status: Literal["verified"]
    published_at: AwareDatetime
    time_precision: Literal["exact", "date"]
    collected_at: AwareDatetime
    publication_basis: str


class NewsItem(DraftItem):
    event_id: str
    published_at: AwareDatetime
    time_precision: Literal["exact", "date"]
    collected_at: AwareDatetime
    is_backfill: bool
    previous_summary: str | None
    sources: list[Citation]
    weighted_score: float
    independent_source_count: int
    verification_limitations: list[str]


class Trend(StrictModel):
    text: str = Field(min_length=3, max_length=1000)
    event_ids: list[str] = Field(min_length=1)
    label: Literal["ANALYSIS"]


class Synthesis(StrictModel):
    executive_summary: str = Field(min_length=10, max_length=2000)
    top_stories: list[str] = Field(max_length=5)
    trend_signals: list[Trend] = Field(max_length=3)


class SynthesisReview(StrictModel):
    approved: bool
    unsupported_claims: list[str]


class Coverage(StrictModel):
    source_status: list[SourceStatus]
    categories_checked: list[Category]
    categories_missing: list[Category]
    regions_checked: list[str]
    limitations: list[str]


class Quality(StrictModel):
    status: Literal["passed"]
    candidates_collected: int
    candidates_verified: int
    published_count: int
    duplicates_removed: int
    rejected_count: int
    limitations: list[str]


class Report(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    report_date: date
    window_start: AwareDatetime
    window_end: AwareDatetime
    backfill_start: AwareDatetime
    generated_at: AwareDatetime
    timezone: str
    executive_summary: str
    top_stories: list[str]
    news_items: list[NewsItem]
    open_source_items: list[NewsItem]
    trend_signals: list[Trend]
    tracking_updates: list[str]
    coverage_report: Coverage
    quality_report: Quality
    usage: list[dict]
    estimated_cost_usd: float
    prompt_sha256: str

    @property
    def all_items(self):
        return self.news_items + self.open_source_items
