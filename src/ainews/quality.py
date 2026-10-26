from datetime import timedelta

from .models import Coverage
from .net import normalize_url

CATEGORIES = ["models", "agents", "opensource", "business", "applications", "infrastructure"]


class QualityError(RuntimeError):
    """Safe, non-sensitive quality gate error."""


class FatalAPIError(QualityError):
    """Authentication/configuration errors cannot be repaired by trying another news source."""


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
    if settings.discovery_provider == "aihot":
        return check_aihot_coverage(statuses, settings)
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
    return Coverage(source_status=statuses, categories_checked=sorted(checked), categories_missing=sorted(missing),
                    regions_checked=sorted(regions), limitations=[f"{s.name}: {s.error}" for s in statuses if s.status != "ok"])


def check_aihot_coverage(statuses, settings):
    enabled = [s for s in statuses if s.status != "disabled"]
    if any(s.kind != "aihot" for s in enabled) or len({s.source_id for s in enabled}) != len(enabled):
        raise QualityError("AIHOT coverage requires unique AIHOT discovery sources")
    good = [s for s in enabled if s.status == "ok"]
    if not enabled or len(good) / len(enabled) < settings.min_source_success_ratio:
        raise QualityError("Insufficient successful AIHOT source coverage")
    by_id = {s.source_id: s for s in good}
    checked = {c for c in CATEGORIES if "aihot_"+c in by_id and by_id["aihot_"+c].categories == [c]}
    missing = set(CATEGORIES) - checked
    if missing:
        raise QualityError("Effective AIHOT query missing for categories: " + ", ".join(sorted(missing)))
    if "search_backfill" not in by_id:
        raise QualityError("AIHOT 72-hour major-event backfill query unavailable")
    limitations = ["候选统一来自 AIHOT 单一发现入口；六领域查询不代表独立全球多源覆盖，地区覆盖未知。"]
    limitations.extend(f"{s.name}: {s.error or s.warning or s.status}" for s in statuses
                       if s.status != "ok" or s.warning)
    return Coverage(source_status=statuses, categories_checked=sorted(checked), categories_missing=[],
                    regions_checked=[], limitations=limitations)


def require_verified(items, attempted_count, settings):
    if len(items) < settings.min_verified_candidates:
        raise QualityError("Too few candidates with readable original sources")
    if attempted_count and (attempted_count-len(items))/attempted_count > settings.max_verification_failure_ratio:
        raise QualityError("Original-source verification failure ratio exceeded")
    regions = {c.region for c in items}
    if set(settings.required_regions) - regions:
        raise QualityError("Verified source coverage missing required geographic regions")
