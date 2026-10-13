from datetime import timedelta

from .models import Coverage
from .net import normalize_url

CATEGORIES = ["models", "agents", "opensource", "business", "applications", "infrastructure"]


class QualityError(RuntimeError):
    """Safe, non-sensitive quality gate error."""


def filter_candidates(items, start, end):
    accepted, rejected, seen = [], [], set()
    for item in items:
        reason = None
        if item.published_at is None:
            reason = "unknown_publication_time"
        elif item.time_precision == "date":
            # Date-only timestamp is the start of the source's local day, not an exact publication instant.
            if item.published_at < start or item.published_at + timedelta(days=1) > end:
                reason = "date_precision_overlaps_window_boundary"
        elif not start <= item.published_at < end:
            reason = "outside_window"
        if reason:
            rejected.append({"candidate_id": item.id, "reason": reason})
            continue
        # Keep the most informative copy when two feeds point to the same original.
        url = normalize_url(item.url)
        if url not in seen:
            seen.add(url)
            accepted.append(item)
    return accepted, rejected


def check_coverage(statuses, settings):
    enabled = [s for s in statuses if s.status not in {"disabled", "skipped"}]
    good = [s for s in enabled if s.status == "ok"]
    if not enabled or len(good) / len(enabled) < settings.min_source_success_ratio:
        raise QualityError("Insufficient successful source coverage")
    searches = [s for s in statuses if s.kind == "web_search" and s.source_id != "search_backfill"]
    checked = {c for s in searches if s.status == "ok" for c in s.categories}
    missing = set(CATEGORIES) - checked
    if missing:
        raise QualityError("Effective web search missing for categories: " + ", ".join(sorted(missing)))
    backfill = [s for s in statuses if s.source_id == "search_backfill"]
    if not backfill or backfill[0].status != "ok":
        raise QualityError("Third-round major-event backfill search unavailable")
    regions = {s.region for s in good if s.region != "global"}
    # Search can supplement failed geographic feeds, but its result must actually be assigned to that region.
    regions |= {r for s in good for r in getattr(s, "result_regions", [])}
    return Coverage(source_status=statuses, categories_checked=sorted(checked), categories_missing=sorted(missing),
                    regions_checked=sorted(regions), limitations=[f"{s.name}: {s.error}" for s in statuses if s.status != "ok"])


def require_verified(items, attempted_count, settings):
    if len(items) < settings.min_verified_candidates:
        raise QualityError("Too few candidates with readable original sources")
    if attempted_count and (attempted_count-len(items))/attempted_count > settings.max_verification_failure_ratio:
        raise QualityError("Original-source verification failure ratio exceeded")
    regions = {c.region for c in items}
    if set(settings.required_regions) - regions:
        raise QualityError("Verified source coverage missing required geographic regions")
