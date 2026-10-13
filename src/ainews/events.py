import copy
import hashlib
import re
import unicodedata
from datetime import datetime, timedelta

from .net import normalize_url
from .quality import QualityError


def identity(value):
    return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", value).casefold())


def evidence_hash(item):
    return hashlib.sha256(item.excerpt.encode()).hexdigest()


class EventStore:
    def __init__(self, state):
        if state.get("version") != 1 or not isinstance(state.get("events"), dict) or not isinstance(state.get("reports"), dict):
            raise QualityError("Unsupported or damaged event state")
        self.state = copy.deepcopy(state)

    def history(self, end, days=30):
        cutoff = end-timedelta(days=days)
        return {key: event for key, event in self.state["events"].items()
                if datetime.fromisoformat(event["last_seen_at"]) >= cutoff}

    def match(self, item, candidates):
        urls = {normalize_url(c.url) for c in candidates}
        events = self.state["events"]
        existing = None
        event_id = "evt_" + hashlib.sha256(identity(item.event_key).encode()).hexdigest()[:20]
        if item.previous_event_id:
            if item.previous_event_id not in events:
                raise QualityError("Unknown previous_event_id")
            event_id, existing = item.previous_event_id, events[item.previous_event_id]
        else:
            for key, event in events.items():
                if identity(item.event_key) in event["aliases"] or urls.intersection(event["urls"]):
                    event_id, existing = key, event
                    break
        if existing:
            same_milestone = identity(item.milestone) in existing["milestones"]
            new_evidence = any(evidence_hash(c) not in existing["evidence_hashes"] for c in candidates)
            duplicate = same_milestone or not item.is_followup or not item.new_information.strip() or not new_evidence
            return event_id, existing, duplicate
        if item.is_followup:
            raise QualityError("Follow-up has no matching historical event")
        return event_id, None, False

    def remember(self, item, candidates, event_id, report_date):
        event = self.state["events"].setdefault(event_id, {
            "event_key": item.event_key, "aliases": [], "urls": [], "milestones": [], "evidence_hashes": [],
            "summary": "", "first_report": report_date, "last_report": report_date,
            "last_seen_at": max(c.collected_at for c in candidates).isoformat(), "updates": [],
        })
        for field, values in {"aliases": [identity(item.event_key)], "urls": [normalize_url(c.url) for c in candidates],
                              "milestones": [identity(item.milestone)], "evidence_hashes": [evidence_hash(c) for c in candidates]}.items():
            event[field] = sorted(set(event[field]) | set(values))
        event["summary"] = item.summary
        event["last_report"] = report_date
        event["last_seen_at"] = max(c.collected_at for c in candidates).isoformat()
        event["updates"].append({"report_date": report_date, "milestone": item.milestone,
                                 "new_information": item.new_information, "summary": item.summary})
