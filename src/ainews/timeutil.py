import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.parser import parse


def parse_time(value, timezone=None):
    if not value:
        return None, "unknown"
    try:
        text = str(value).strip()
        precision = "exact" if re.search(r"(?:T|\s)\d{1,2}:\d{2}", text) else "date"
        result = parse(text)
        if result.tzinfo is None:
            if not timezone:
                return None, "unknown"
            result = result.replace(tzinfo=ZoneInfo(timezone))
        return result.astimezone(UTC), precision
    except (ValueError, OverflowError, TypeError):
        return None, "unknown"


def report_window(day: date, timezone: str, hour: int):
    end = datetime.combine(day, time(hour), ZoneInfo(timezone)).astimezone(UTC)
    return end - timedelta(days=1), end


def research_window(now: datetime, state: dict, day: date):
    previous = [datetime.fromisoformat(r["window_end"]) for d, r in state["reports"].items() if d < day.isoformat()]
    start = max(previous) if previous else now - timedelta(days=1)
    if start >= now:
        raise ValueError("Invalid report window: historical cutoff is in the future")
    return start, now
