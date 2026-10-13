from .events import identity
from .models import Citation, EditorialBatch, NewsItem, Review, Synthesis, SynthesisReview
from .net import normalize_url, validate_url
from .quality import QualityError


class Editor:
    def __init__(self, ai, prompt, settings, sources):
        self.ai, self.prompt, self.settings = ai, prompt, settings
        self.sources = {s.id: s for s in sources}
        self.rejections = []
        self.duplicates = 0

    def edit(self, candidates, events, context):
        by_id = {c.id: c for c in candidates}
        drafts = []
        for offset in range(0, len(candidates), self.settings.batch_size):
            batch = candidates[offset:offset+self.settings.batch_size]
            result = self.ai.request(EditorialBatch, self.prompt + "\nThis call edits one candidate batch. "
                                     "Use candidate_ids instead of inventing URLs. Use stable English event_key "
                                     "(entity:product/version:event family) and milestone (specific new fact). "
                                     "Match HISTORY event IDs and existing milestones. For duplicates reject candidates. "
                                     "Every candidate must appear in news_items or rejected_candidate_ids. "
                                     "No publication dates: program derives them from candidates. "
                                     "UNVERIFIED and P3 must be rejected. Unknown occurred_at must be null.",
                                     {**context, "CANDIDATES": [c.model_dump(mode="json") for c in batch]},
                                     self.settings.mini_model, "candidate_edit")
            input_ids = {c.id for c in batch}
            used = {cid for d in result.news_items for cid in d.candidate_ids}
            rejected = set(result.rejected_candidate_ids)
            if used | rejected != input_ids or used & rejected:
                raise QualityError("Editor candidate accounting mismatch")
            self.rejections.extend({"candidate_id": cid, "reason": "editor_rejected"} for cid in rejected)
            drafts.extend(result.news_items)
        # Consolidate same-event drafts spanning candidate batches into one core story.
        grouped = {}
        for item in drafts:
            key = item.previous_event_id or identity(item.event_key)
            grouped.setdefault(key, []).append(item)
        drafts = []
        for group in grouped.values():
            if len(group) == 1:
                drafts.extend(group)
                continue
            ids = {cid for d in group for cid in d.candidate_ids}
            result = self.ai.request(EditorialBatch, self.prompt + "\nConsolidate these drafts about ONE underlying "
                                     "event into at most one core story. Combine substantive increments, retain "
                                     "source IDs and tags. A same-day announcement and release are one story, "
                                     "not cross-period follow-ups. Only HISTORY can supply previous_event_id.",
                                     {**context, "DRAFTS": [d.model_dump(mode="json") for d in group],
                                      "CANDIDATES": [by_id[cid].model_dump(mode="json") for cid in ids]},
                                     self.settings.mini_model, "event_consolidation")
            if len(result.news_items) > 1 or any(cid not in ids for d in result.news_items for cid in d.candidate_ids):
                raise QualityError("Same-event consolidation failed")
            accounted = {cid for d in result.news_items for cid in d.candidate_ids} | set(result.rejected_candidate_ids)
            if accounted != ids:
                raise QualityError("Consolidation candidate accounting mismatch")
            drafts.extend(result.news_items)
        # Sort high-priority news first. max_items never suppresses P0/P1.
        drafts.sort(key=lambda d: (d.priority, -d.score.weighted))
        accepted = []
        for item in drafts:
            if item.priority == "P3" or item.fact_status != "FACT":
                self.rejections.append({"candidate_ids": item.candidate_ids, "reason": "unverified_or_low_value"})
                continue
            evidence = [by_id[cid] for cid in item.candidate_ids]
            if item.priority in {"P0", "P1"}:
                refined = self.ai.request(EditorialBatch, self.prompt + "\nSecond round: critically re-edit this "
                                          "major event using original evidence. Return at most one news item. "
                                          "Do not expand candidate_ids beyond supplied sources. Retain original "
                                          "event_key, milestone and historical match unless correcting an error.",
                                          {**context, "DRAFT": item.model_dump(mode="json"),
                                           "CANDIDATES": [c.model_dump(mode="json") for c in evidence]},
                                          self.settings.major_model, "major_event_edit")
                if len(refined.news_items) > 1:
                    raise QualityError("Major event refinement returned multiple events")
                if not refined.news_items:
                    self.rejections.append({"candidate_ids": item.candidate_ids, "reason": "major_editor_rejected"})
                    continue
                item = refined.news_items[0]
                if set(item.candidate_ids) - {c.id for c in evidence}:
                    raise QualityError("Major editor invented evidence IDs")
                evidence = [by_id[cid] for cid in item.candidate_ids]
            if item.priority == "P3" or item.fact_status != "FACT":
                continue
            if not all(c.verified and c.published_at for c in evidence):
                raise QualityError("Editorial output references unverified candidates")
            if item.occurred_at and item.occurred_at >= context["WINDOW_END"]:
                raise QualityError("Future event presented as occurred fact")
            review = self.ai.request(Review, self.prompt + "\nYou are an independent verification pass. "
                                     "Check every factual claim and number in DRAFT against original CANDIDATES. "
                                     "Do not approve facts found only in the title. Ensure ANALYSIS is clearly "
                                     "inference. Check contradictions and occurred_at support. For followups compare "
                                     "HISTORY and require substantive new_information. An official statement and "
                                     "its reposts count as one independent source. Be conservative. Reject if not supported.",
                                     {**context, "DRAFT": item.model_dump(mode="json"),
                                      "CANDIDATES": [c.model_dump(mode="json") for c in evidence]},
                                     self.settings.major_model if item.priority in {"P0", "P1"} else self.settings.mini_model,
                                     "fact_verification")
            if set(review.supported_candidate_ids) - {c.id for c in evidence}:
                raise QualityError("Verifier invented evidence IDs")
            if not review.approved or review.issues or not review.supported_candidate_ids:
                self.rejections.append({"candidate_ids": item.candidate_ids, "reason": "facts_not_supported"})
                # A high-priority event cannot silently disappear after factual verification fails.
                if item.priority in {"P0", "P1"}:
                    raise QualityError("Major event factual verification failed")
                continue
            evidence = [by_id[cid] for cid in review.supported_candidate_ids]
            if item.occurred_at and not review.occurred_at_supported:
                raise QualityError("Occurred time is unsupported")
            if item.is_followup and not review.new_information_supported:
                raise QualityError("Event follow-up lacks supported new information")
            event_id, previous, duplicate = events.match(item, evidence)
            if duplicate:
                self.duplicates += 1
                continue
            if event_id in {i.event_id for i in accepted}:
                raise QualityError("One event cannot produce multiple core stories in the same report")
            if item.repository_url:
                validate_url(item.repository_url, ["github.com"])
                repo = normalize_url(item.repository_url)
                # GitHub release evidence may be a child of the repository URL.
                if not any(normalize_url(c.url) == repo or normalize_url(c.url).startswith(repo+"/") for c in evidence):
                    raise QualityError("Repository URL is not grounded in source evidence")
            if item.category == "opensource" and any(not v for v in [item.repository_url, item.technical_highlights,
                                                                      item.target_users, item.maturity_limitations]):
                raise QualityError("Open-source item lacks required engineering assessment")
            published = max(c.published_at for c in evidence)
            is_backfill = published < context["WINDOW_START"]
            if is_backfill and item.priority not in {"P0", "P1"}:
                continue
            if len(accepted) >= self.settings.max_items and item.priority == "P2":
                continue
            citations = [Citation(candidate_id=c.id, url=c.url, name=self.sources[c.source_id].name or c.source_id,
                                  source_type=self.sources[c.source_id].kind, verification_status="verified",
                                  published_at=c.published_at, time_precision=c.time_precision,
                                  collected_at=c.collected_at, publication_basis=c.publication_basis) for c in evidence]
            limitations = []
            if review.independent_source_count < 2 and item.priority in {"P0", "P1"}:
                limitations.append("主要依据单一原始来源，尚无独立交叉验证")
            accepted.append(NewsItem(**item.model_dump(), event_id=event_id, published_at=published,
                                     time_precision="date" if any(c.time_precision == "date" for c in evidence) else "exact",
                                     collected_at=max(c.collected_at for c in evidence), is_backfill=is_backfill,
                                     previous_summary=previous["summary"] if previous else None, sources=citations,
                                     weighted_score=item.score.weighted,
                                     independent_source_count=review.independent_source_count,
                                     verification_limitations=limitations))
            events.remember(item, evidence, event_id, context["RUN_DATE"])
        if not accepted:
            # A genuinely quiet day is valid only after real collection/editing; no invented top stories.
            return [], Synthesis(executive_summary="本期已完成检索与核验，暂无符合收录标准的新事件。", top_stories=[], trend_signals=[])
        synthesis = self.ai.request(Synthesis, self.prompt + "\nSynthesize ONLY these verified news items. "
                                    "Reference their stable event_id in top_stories and trends. Do not add new facts. "
                                    "All trend_signals are explicitly ANALYSIS, tied to accepted items.",
                                    {"NEWS_ITEMS": [i.model_dump(mode="json") for i in accepted]},
                                    self.settings.mini_model, "report_synthesis")
        valid_ids = {i.event_id for i in accepted}
        refs = set(synthesis.top_stories) | {key for trend in synthesis.trend_signals for key in trend.event_ids}
        if refs - valid_ids or len(synthesis.top_stories) != len(set(synthesis.top_stories)):
            raise QualityError("Report synthesis contains unknown or duplicated event references")
        verification = self.ai.request(SynthesisReview,
                                       "Independently check every factual claim and number in SYNTHESIS against "
                                       "VERIFIED_NEWS. Reject unsupported new facts and contradictions. Trends "
                                       "must be explicitly labeled ANALYSIS and grounded in referenced events.",
                                       {"SYNTHESIS": synthesis.model_dump(mode="json"),
                                        "VERIFIED_NEWS": [i.model_dump(mode="json") for i in accepted]},
                                       self.settings.mini_model, "synthesis_verification")
        if not verification.approved or verification.unsupported_claims:
            raise QualityError("Synthesis factual verification failed")
        return accepted, synthesis
