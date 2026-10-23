# AIHOT Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AIHOT alone discovers news; existing original-source verification and archives remain authoritative.

**Architecture:** A small read-only MCP adapter consumes structuredContent, with logged fallback to AIHOT JSON endpoints. Configured AIHOT source groups feed the existing Collector and Work pipeline. Provenance is additive and old archives stay valid.

**Tech Stack:** Existing Python, httpx, Pydantic, pytest, Jinja2; no additional paid API or runtime dependencies.

## Task 1: Transport and candidate mapping
- [ ] Write failing tests in `tests/test_aihot.py` for JSON/SSE RPC, structured data only, allowed tools, fallback and Retry-After.
- [ ] Add `src/ainews/aihot.py`; retain bounded reads, validated URLs, no remote instructions.
- [ ] Extend `models.py` and `collectors.py` with AIHOT settings/provenance; test original date and link validation.

## Task 2: Source-only quality and import constraints
- [ ] Test AIHOT six-domain/backfill coverage and rejection of extra discoveries/paid run mode.
- [ ] Update `config.py`, `quality.py`, `work.py`, `pipeline.py` and `editor.py`; preserve legacy defaults and archive compatibility.
- [ ] Switch `sources.yaml`/`settings.yaml`; retain legacy source config as explicit reference only.

## Task 3: Prompt, operating guide and preview
- [ ] Replace contradictory multi-source/individual-account/Trending instructions in current prompt and manuals.
- [ ] Show optional discovery provenance in template; update schemas and CLI probe command.
- [ ] Real MCP probe and original-source evidence smoke test in ignored logs, without publishing a fake or duplicate report.

## Task 4: Verification and activation
- [ ] Full pytest, lint, config/history/schema checks; independent review and fixes.
- [ ] Update existing two heartbeat prompts through automation tool, preserving paused status and actual schedules.
- [ ] Commit/push authorized project changes; report verified integrations and remaining limits.
