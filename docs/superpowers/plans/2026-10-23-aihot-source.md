# AIHOT Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** AIHOT alone discovers news; existing original-source verification and archives remain authoritative.

**Architecture:** A small read-only MCP adapter consumes structuredContent, with logged fallback to AIHOT JSON endpoints. Configured AIHOT source groups feed the existing Collector and Work pipeline. Provenance is additive and old archives stay valid.

**Tech Stack:** Existing Python, httpx, Pydantic, pytest, Jinja2; no additional paid API or runtime dependencies.

## Task 1: Transport and candidate mapping
- [x] Write failing tests in `tests/test_aihot_transport.py` for JSON/SSE RPC, structured data only, allowed tools, fallback and Retry-After.
- [x] Add `src/ainews/aihot.py`; retain bounded reads, validated URLs, no remote instructions.
- [x] Extend `models.py` and `collectors.py` with AIHOT settings/provenance; test original date and link validation.

## Task 2: Source-only quality and import constraints
- [x] Test AIHOT six-domain/backfill coverage and rejection of extra discoveries/paid run mode.
- [x] Update `config.py`, `quality.py`, `work.py`, `pipeline.py` and `editor.py`; preserve legacy defaults and archive compatibility.
- [x] Switch `sources.yaml`/`settings.yaml`; retain legacy source config as explicit reference only.

## Task 3: Prompt, operating guide and preview
- [x] Replace contradictory multi-source/individual-account/Trending instructions in current prompt and manuals.
- [x] Show optional discovery provenance in template; update schemas and CLI probe command.
- [x] Real MCP probe and original-source evidence smoke test in ignored logs, without publishing a fake or duplicate report.

## Task 4: Verification and activation
- [x] Full pytest, lint, config/history/schema checks; independent review and fixes.
- [x] Update existing two heartbeat prompts through automation tool, preserving paused status and actual schedules.
- [x] Commit/push authorized project changes; report verified integrations and remaining limits.

## 验证结果（2026-10-26）

181 tests passed；Ruff及diff检查通过；规格/质量独立审查通过。真实MCP精选成功、Decoder原文日期核验成功；Browser Use缺乏通用日期元数据时正确拒绝。36份历史/状态/首页文件逐字保持不变。任务提示词已更新，暂停及09:00工作日/10:30每日计划保留。详见docs/validation.md。
