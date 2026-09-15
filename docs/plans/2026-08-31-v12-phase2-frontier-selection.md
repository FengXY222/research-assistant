# 科研助手 v12.0 阶段二：每日前沿与选刊 Implementation Plan

> **For agentic workers:** REQUIRED SKILLS: use `test-driven-development` for each rule, `systematic-debugging` for network/UI failures, and `verification-before-completion` at the phase gate. Execute inline after the phase-one report passes.

**Goal:** 用长期/短期画像和真实论文证据重建每日前沿与为论文选刊，同时保持小组件清晰、EasyScholar 门槛严谨、AI 全程可见。

**Architecture:** 使用标准库 SQLite 缓存可重建证据；现有 Crossref/OpenAlex/Semantic Scholar/DOAJ/arXiv 适配逻辑统一返回同一工作记录。每日前沿先召回和质量分流再由 AI 解释；选刊先从相似真实论文生成期刊，再验证身份和硬条件，AI 不能绕过门槛。

**Tech Stack:** Python 3.12、PySide6、sqlite3、urllib/现有网络帮助函数、EasyScholar、Qt offscreen/QTest。

**Spec:** `docs/specs/2026-08-31-v12-research-intelligence-design.md`

## Global Constraints

- 阶段一验收报告必须为通过状态。
- 只保留小组件模式；完整选刊继续从 PAPERS 打开独立 1024x768 工作台。
- 配置 EasyScholar 时，每日前沿主流只显示已核验 JCR Q1/Q2；未知分区不推送。
- 未配置 EasyScholar 时不根据分区淘汰每日前沿或选刊候选。
- 已拒稿、停刊、身份无法核验、重复和出版社不匹配是选刊硬淘汰。
- 费用和预计速度只排序，不淘汰；混合 OA 同时匹配付费和不付费。
- 本阶段不得生成或安装中间版本。
- Python 命令统一使用 `S:\Python\Scripts\python.exe`。
- 当前目录不是 Git 仓库；以测试和 QA 报告作为检查点。

## File Map

- Create `utils/evidence_cache.py`: 共享 SQLite 缓存和模式迁移。
- Create `utils/research_signal_service.py`: 长短期画像、信号衰减和一句话评价应用。
- Modify `utils/frontier_service.py`: 多源召回、推荐 API、去重和主流/预印本分流。
- Modify `utils/frontier_scoring.py`: 核心比例、探索推荐和质量门槛。
- Modify `utils/file_manager.py`: v12 前沿状态、信号和画像引用兼容。
- Modify `utils/ai_service.py`: 一句话评价、前沿解释和相似期刊补充契约。
- Modify `ui/frontier_page.py`: 双信息流、推荐理由、评价和完整状态。
- Modify `ui/workbench_shell.py`: LIBRARY 默认前沿路由保持稳定。
- Modify `utils/journal_service.py`: 期刊身份与来源证据。
- Modify `utils/journal_health_service.py`: 停刊和身份状态。
- Modify `utils/journal_selection_service.py`: 相似论文候选、硬门槛、多轮停止和总分。
- Modify `ui/journal_selection_dialog.py`: 条件交互、进度、结果证据和卡片动作。
- Modify `ui/paper_page.py`: 传递当前论文并保持快捷入口。
- Modify `ui/theme.py`: 前沿与选刊的 v12 行式 UI。
- Create `tests/test_v12_evidence_cache.py`.
- Create `tests/test_v12_research_signals.py`.
- Create `tests/test_v12_frontier_discovery.py`.
- Create `tests/test_v12_frontier_quality.py`.
- Create `tests/test_v12_frontier_ui.py`.
- Create `tests/test_v12_journal_discovery.py`.
- Create `tests/test_v12_journal_selection.py`.
- Create `tests/test_v12_journal_selection_ui.py`.
- Extend `scripts/render_v12_ui.py`.
- Create `docs/qa/v12-phase2-acceptance.md`.

---

### Task 1: Add A Rebuildable Evidence Cache

**Files:**
- Create: `utils/evidence_cache.py`
- Modify: `utils/file_manager.py`
- Create: `tests/test_v12_evidence_cache.py`

**Interfaces:**
- Produces: `EvidenceCache(path: Path)`.
- Produces: `EvidenceCache.initialize() -> None`.
- Produces: `EvidenceCache.put_source_response(source: str, query_key: str, payload: dict[str, Any], fetched_at: str, expires_at: str) -> None`.
- Produces: `EvidenceCache.get_source_response(source: str, query_key: str, *, now: str) -> dict[str, Any] | None`.
- Produces: `EvidenceCache.upsert_work(work: dict[str, Any]) -> str`.
- Produces: `EvidenceCache.put_journal_evidence(journal_key: str, evidence: dict[str, Any]) -> None`.
- Produces: `EvidenceCache.clear_rebuildable_data() -> None`.
- Produces: `EvidenceCache.integrity_check() -> str`, returning `"ok"` only after SQLite `PRAGMA integrity_check` succeeds.

- [ ] **Step 1: Write failing schema, expiry and corruption tests**

```python
def test_expired_source_response_is_not_returned(tmp_path):
    cache = EvidenceCache(tmp_path / "research_intelligence.sqlite")
    cache.initialize()
    cache.put_source_response("openalex", "soil", {"results": [1]}, "2026-08-01", "2026-08-02")
    assert cache.get_source_response("openalex", "soil", now="2026-08-03") is None

def test_cache_can_be_rebuilt_without_personal_json(tmp_path):
    cache = EvidenceCache(tmp_path / "research_intelligence.sqlite")
    cache.initialize()
    cache.clear_rebuildable_data()
    assert cache.integrity_check() == "ok"
```

- [ ] **Step 2: Run cache tests and confirm the module is absent**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_evidence_cache.py -q
```

- [ ] **Step 3: Implement a small sqlite3 wrapper**

Create schema-version table plus `source_responses`, `works`, `journal_evidence`, `ocr_pages`, `special_issue_discovery`, `special_issue_verification`, `dedupe_keys`, and `job_checkpoints`. Use one connection per operation, WAL mode and parameterized SQL. Keep JSON payloads UTF-8 encoded with deterministic key ordering.

- [ ] **Step 4: Add atomic schema migration and integrity check**

Run migrations inside `BEGIN IMMEDIATE`; rollback on failure. A broken cache is renamed with `.broken-<timestamp>` and rebuilt. Do not touch personal JSON.

- [ ] **Step 5: Run cache and data-root tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_evidence_cache.py tests/test_v12_storage_bootstrap.py tests/test_data_root_and_legacy_safety.py -q
```

- [ ] **Step 6: Record the checkpoint**

Record schema version, integrity result and rebuild test in `docs/qa/v12-phase2-acceptance.md`.

---

### Task 2: Implement Long-Term, Short-Term And Natural-Language Signals

**Files:**
- Create: `utils/research_signal_service.py`
- Modify: `utils/research_profile_service.py`
- Modify: `utils/ai_service.py`
- Modify: `ui/frontier_page.py`
- Create: `tests/test_v12_research_signals.py`

**Interfaces:**
- Produces: `record_signal(profile: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]`.
- Produces: `build_profile_view(profile: dict[str, Any], *, now: datetime) -> dict[str, Any]` with `long_term`, `short_term`, `positive_seeds`, `negative_seeds`.
- Produces: `classify_profile_comment_with_ai(text: str, context: dict[str, Any], progress: Callable[..., None] | None = None) -> dict[str, Any]`.
- Produces: `apply_profile_comment(profile: dict[str, Any], classification: dict[str, Any], *, today: str) -> tuple[dict[str, Any], list[dict[str, Any]]]`.

- [ ] **Step 1: Write failing signal-strength and neutral-no-action tests**

Use these fixed coefficients in tests and implementation:

- Authored paper and locked term: weight 1.0, no decay.
- Favorite and paper association: `+1.0`, 180-day half-life.
- Detail open and read: `+0.35`, 45-day half-life.
- Explicit ignore: `-1.0`, 180-day half-life.
- Clear one-line preference: `+1.0` or `-1.0`, 365-day half-life.
- No action: no event and no score.

```python
def test_no_action_is_neutral():
    view = build_profile_view(profile_without_events, now=datetime(2026, 8, 31))
    assert view["short_term"] == []

def test_locked_terms_and_authored_papers_do_not_decay():
    view = build_profile_view(profile_with_old_locked_signal, now=datetime(2036, 8, 31))
    assert view["long_term"][0]["strength"] == 1.0
```

- [ ] **Step 2: Run signal tests and confirm missing service**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_research_signals.py -q
```

- [ ] **Step 3: Implement deterministic decay and seed lists**

Use `strength * 0.5 ** (age_days / half_life_days)`. Preserve signed strength. Do not infer negatives from missing events.

- [ ] **Step 4: Implement structured one-line classification**

Return `intent` (`positive`, `negative`, `ambiguous`), `active_terms`, `excluded_terms`, `pending_terms`, `reason`, and `confidence`. Clear positive/negative intent applies immediately through the profile transaction; ambiguous terms enter pending.

- [ ] **Step 5: Wire feedback actions to signal events**

Favorite/associate/ignore/read/detail-open produce exactly one deduplicated event per item/action/day. Existing relevance fields remain readable for migration but new ranking consumes v12 signals.

- [ ] **Step 6: Run profile, signal and AI contract tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_research_signals.py tests/test_v12_profile_repository.py tests/test_v12_profile_ai_contracts.py -q
```

- [ ] **Step 7: Record the checkpoint**

Include the two user examples about soil microorganisms and soil heavy-metal mapping as invented contract fixtures, not personal log records.

---

### Task 3: Rebuild Daily Frontier Discovery, Quotas And Quality Gates

**Files:**
- Modify: `utils/frontier_service.py`
- Modify: `utils/frontier_scoring.py`
- Modify: `utils/file_manager.py`
- Modify: `utils/easyscholar_service.py`
- Modify: `utils/journal_quality.py`
- Modify: `utils/ai_service.py`
- Create: `tests/test_v12_frontier_discovery.py`
- Create: `tests/test_v12_frontier_quality.py`
- Modify: `tests/test_frontier_service_v11_integration.py`
- Modify: `tests/test_v115_frontier_quality.py`

**Interfaces:**
- Produces: `discover_frontier_candidates(profile_view: dict[str, Any], *, cache: EvidenceCache, progress: Callable[..., None] | None = None) -> list[dict[str, Any]]`.
- Produces: `dedupe_works(items: list[dict[str, Any]]) -> list[dict[str, Any]]`.
- Produces: `partition_frontier_items(items: list[dict[str, Any]], profile: dict[str, Any], *, easyscholar_ready: bool) -> dict[str, list[dict[str, Any]]]` with `journal`, `preprint`, `pending_quality`.
- Produces: `select_daily_mix(items: list[dict[str, Any]], *, limit: int, minimum_core_ratio: float = 0.5) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write failing dedupe, ratio, fallback and quality tests**

Assert DOI beats source IDs, normalized title is final fallback, core items occupy at least `ceil(limit * 0.5)` when available, profile exploration fills missing slots, and configured EasyScholar excludes unknown/Q3/Q4 from `journal` while preserving them in `pending_quality`.

- [ ] **Step 2: Run focused frontier tests and confirm old pair-gate behavior fails**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_frontier_discovery.py tests/test_v12_frontier_quality.py -q
```

- [ ] **Step 3: Normalize source records**

Every source record must include `source`, `source_id`, `doi`, `title`, `abstract`, `authors`, `published_date`, `journal`, `issn`, `publisher`, `url`, `is_preprint`, `fetched_at`, and source evidence. Reuse current adapters and cache helpers instead of rewriting HTTP clients.

- [ ] **Step 4: Add Semantic Scholar positive/negative seed recommendations**

Use authored/saved/associated works as positive seeds and explicit ignored works as negative seeds. Fall back to keyword queries when a recommendation API is unavailable. Cache responses and surface source failures without aborting other adapters.

- [ ] **Step 5: Apply the quality gate before AI explanation**

With EasyScholar ready, accept only verified Q1/Q2 or a local library record whose JCR verification status is explicit and current. With EasyScholar absent, do not filter by quartile. Preprints bypass journal quality only into the separate preprint stream.

- [ ] **Step 6: Generate concise recommendation reasons**

AI returns one reason and matched evidence. Local fallback reason uses matched locked/core terms or positive-paper similarity. Every exploration result receives `recommendation_kind="profile_exploration"`.

- [ ] **Step 7: Run new and legacy frontier tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_frontier_discovery.py tests/test_v12_frontier_quality.py tests/test_frontier_service_v11_integration.py tests/test_frontier_scoring_v11.py tests/test_v115_frontier_quality.py -q
```

- [ ] **Step 8: Record the checkpoint**

Record deterministic fixture counts for journal/preprint/pending-quality and the achieved core ratio.

---

### Task 4: Redesign The Daily Frontier Widget

**Files:**
- Modify: `ui/frontier_page.py`
- Modify: `ui/workbench_shell.py`
- Modify: `ui/main_window.py`
- Modify: `ui/theme.py`
- Create: `tests/test_v12_frontier_ui.py`
- Extend: `scripts/render_v12_ui.py`

**Interfaces:**
- UI object names: `frontierJournalTab`, `frontierPreprintTab`, `frontierPendingQualityButton`, `frontierRecommendationRow`, `frontierReason`, `frontierCommentEdit`, `frontierCommentSubmit`, `frontierRefreshProgress`.
- Keeps: `DailyFrontierPage.auto_refresh_if_due()` and `DailyFrontierPage.reload()`.

- [ ] **Step 1: Write failing 400x480, tab and state tests**

Assert LIBRARY defaults to frontier, journal/preprint tabs exist, pending-quality items are absent from the main list, a long title does not overlap action buttons, comment submission shows progress, and loading/empty/error/cancel states are reachable.

- [ ] **Step 2: Run the UI tests and confirm the current single-list UI fails**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_frontier_ui.py -q
```

- [ ] **Step 3: Build a row-based compact feed**

Use a stable header, two tabs, a status/progress strip and one scrolling list. Each row shows title, source/date, JCR/CAS where applicable, one reason and compact icon actions. Do not nest cards.

- [ ] **Step 4: Add immediate one-line feedback**

Open the comment editor in the selected item detail. Disable submit while AI classifies, display a determinate stage, apply clear intent transactionally, and keep ambiguous terms pending.

- [ ] **Step 5: Preserve all old feedback actions through v12 signals**

Favorite, read, irrelevant and too-broad actions remain available and produce the v12 event types defined in Task 2.

- [ ] **Step 6: Run frontier UI, layout and theme tests**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_frontier_ui.py tests/test_frontier_ui_contract_v11.py tests/test_v115_cross_page_ui.py tests/test_theme_settings_v11.py -q
```

- [ ] **Step 7: Render widget screenshots**

Render daily journal, preprint, loading, empty and error states at 400x480 and 480x720 for every theme. Add geometry/pixel checks to the phase-two report.

- [ ] **Step 8: Record the checkpoint**

State that unknown-quality journal items remain stored but are not visible in the configured-EasyScholar main feed.

---

### Task 5: Generate Journal Candidates From Similar Real Papers And Verify Identity

**Files:**
- Modify: `utils/journal_service.py`
- Modify: `utils/journal_health_service.py`
- Modify: `utils/publisher_utils.py`
- Modify: `utils/journal_selection_service.py`
- Modify: `utils/ai_service.py`
- Create: `tests/test_v12_journal_discovery.py`
- Modify: `tests/test_v1156_journal_actions.py`

**Interfaces:**
- Produces: `find_similar_works(manuscript: dict[str, Any], *, cache: EvidenceCache, progress: Callable[..., None] | None = None) -> list[dict[str, Any]]`.
- Produces: `derive_journal_candidates(works: list[dict[str, Any]]) -> list[dict[str, Any]]`.
- Produces: `verify_journal_identity(candidate: dict[str, Any], *, cache: EvidenceCache) -> dict[str, Any]`.
- Identity result keys: `verified`, `name`, `issns`, `publisher`, `official_url`, `active`, `sources`, `verified_at`, `conflicts`.

- [ ] **Step 1: Write failing similar-work aggregation and identity tests**

Assert the same ISSN from multiple papers becomes one candidate with multiple evidence papers; a name-only AI suggestion cannot pass; publisher/official URL/ISSN are required; inactive journal is rejected; source conflicts remain in evidence.

- [ ] **Step 2: Run discovery tests and confirm current AI-name-first service fails**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_journal_discovery.py -q
```

- [ ] **Step 3: Reuse existing source adapters for similar works**

Query OpenAlex, Semantic Scholar and Crossref with title, keywords and abstract. Store real work IDs, venue, ISSN and landing URL. Never treat AI prose as source evidence.

- [ ] **Step 4: Aggregate candidates by ISSN then normalized title**

Attach all supporting similar papers and occurrence count. Keep source timestamps. A local journal-library match enriches but does not replace external identity evidence.

- [ ] **Step 5: Verify identity before result admission**

Require a real journal record, at least one ISSN, confirmed publisher, confirmed official homepage and active status. Cache the result. Unknown identity remains diagnostic only and never appears in final cards.

- [ ] **Step 6: Permit AI supplementation only through the same verifier**

AI returns candidate names plus proposed reason. The identity verifier ignores unverified AI fields and rebuilds authoritative identity from sources.

- [ ] **Step 7: Run discovery, health and publisher tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_journal_discovery.py tests/test_v1156_journal_actions.py tests/test_journal_quality_v111.py -q
```

- [ ] **Step 8: Record the checkpoint**

Record fixture counts for similar works, unique venues, verified identities and identity rejections.

---

### Task 6: Apply Multi-Round Hard Gates And Transparent Ranking

**Files:**
- Modify: `utils/journal_selection_service.py`
- Modify: `utils/ai_service.py`
- Create: `tests/test_v12_journal_selection.py`
- Modify: `tests/test_v1155_selection_gate.py`
- Modify: `tests/test_v115_journal_selection.py`
- Modify: `tests/test_ai_contracts_v11.py`

**Interfaces:**
- Produces: `normalize_selection_requirements(raw: Any) -> dict[str, Any]` with publisher, fee mode, JCR/CAS sets, speed priority and fit strictness.
- Produces: `topic_fit_threshold(strictness: str) -> int`, mapping `lenient=55`, `balanced=65`, `strict=75`.
- Produces: `run_selection_rounds(manuscript: dict[str, Any], requirements: dict[str, Any], rejected: list[dict[str, Any]], *, cache: EvidenceCache, max_rounds: int = 8, no_growth_limit: int = 2, progress: Callable[..., None] | None = None) -> dict[str, Any]`.
- Produces result keys: `results`, `searched_keys`, `rejected_counts`, `rounds`, `stop_reason`.

- [ ] **Step 1: Write failing hard-gate and stop-condition tests**

Cover rejected-name/ISSN aliases, inactive journals, identity failure, publisher mismatch, EasyScholar JCR/CAS mismatch, topic below threshold, fee/speed non-elimination, hybrid OA dual match, duplicate search keys and two consecutive no-growth rounds.

- [ ] **Step 2: Run selection tests and confirm current soft/unknown rules differ**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_journal_selection.py tests/test_v1155_selection_gate.py -q
```

- [ ] **Step 3: Apply gates in the specified order**

Order: rejected, inactive/identity, duplicate, publisher, configured-EasyScholar divisions, topic threshold. If EasyScholar is not configured, skip JCR/CAS verification and division filtering completely.

- [ ] **Step 4: Implement multi-round exclusion memory**

Each AI prompt includes canonical rejected keys and every searched key from prior rounds. Locally filter again after each response. Stop after two no-growth rounds, source exhaustion or eight rounds. Return all passing candidates without a five-item cap.

- [ ] **Step 5: Calculate one user-facing total score**

Use topic fit as the primary component, verified quality as secondary, and fee/speed only as ranking bonuses. Keep internal breakdown in evidence but expose only total score and reason on the collapsed card.

- [ ] **Step 6: Run all selection and AI contract tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_journal_selection.py tests/test_v1155_selection_gate.py tests/test_v115_journal_selection.py tests/test_ai_contracts_v11.py -q
```

- [ ] **Step 7: Record the checkpoint**

Document one deterministic eight-round ceiling fixture and one two-no-growth stop fixture.

---

### Task 7: Redesign The 1024x768 Selection Workbench And Bind Actions Correctly

**Files:**
- Modify: `ui/journal_selection_dialog.py`
- Modify: `ui/paper_page.py`
- Modify: `ui/theme.py`
- Create: `tests/test_v12_journal_selection_ui.py`
- Modify: `tests/test_v115_journal_selection_ui.py`
- Modify: `tests/test_v1153_selection_workflow.py`
- Modify: `tests/test_v1156_journal_actions.py`
- Extend: `scripts/render_v12_ui.py`

**Interfaces:**
- UI object names: `selectionPublisher`, `selectionFeeMode`, `selectionJcrMulti`, `selectionCasMulti`, `selectionSpeedPriority`, `selectionFitStrictness`, `selectionStartButton`, `selectionProgress`, `selectionResults`, `selectionEvidenceToggle`, `selectionImportButton`, `selectionPathButton`.
- Keeps signals: `journal_import_requested(dict)` and `submission_path_requested(dict)` carrying the selected result object.

- [ ] **Step 1: Write failing filter, geometry, progress and action-identity tests**

Assert 1024x768 layout, true multi-select JCR/CAS values, conditions/results near 50/50, disabled-start reasons, visible stage progress, result text selection, evidence expansion, and that clicking the third result emits its own journal ID while the dialog remains visible.

- [ ] **Step 2: Run UI tests and confirm current interaction failures**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_journal_selection_ui.py -q
```

- [ ] **Step 3: Reuse and correct the existing multi-select control**

Keep menu checkboxes checked across reopen, show a compact `Q1、Q2` summary, and never use display ellipsis as the stored value. Publisher remains a standard dropdown; fee is a three-state segmented control.

- [ ] **Step 4: Build a stable progress surface**

Show stages: similar-paper discovery, venue aggregation, identity verification, EasyScholar verification, AI fit, gate filtering and completion. Include found/verified/eliminated counters. Disable duplicate starts, but never leave a click without status.

- [ ] **Step 5: Render compact result rows with expandable evidence**

Collapsed rows show selectable journal name, total score, reason, publisher, divisions, fee and speed. Evidence expansion shows source papers, Aims & Scope, ISSN, identity sources, EasyScholar and timestamps.

- [ ] **Step 6: Bind all actions to stable result IDs**

Capture `result_id` in each callback and resolve against the current result mapping. Import and path actions emit that exact result, preserve scroll/current row and leave the window open.

- [ ] **Step 7: Run UI, workflow and action regressions**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_journal_selection_ui.py tests/test_v115_journal_selection_ui.py tests/test_v1153_selection_workflow.py tests/test_v1156_journal_actions.py tests/test_paper_dialog_ui_v11.py -q
```

- [ ] **Step 8: Render selection screenshots**

Render empty, searching, partial, results, expanded evidence and error states at 1024x768 for all themes. Fail on clipped filter summaries, hidden buttons or overlapping result text.

- [ ] **Step 9: Record the checkpoint**

Include emitted result IDs from first/middle/last action tests.

---

### Task 8: Phase-Two Data And UI Gate

**Files:**
- Create/Finalize: `docs/qa/v12-phase2-acceptance.md`
- No packaging changes.

**Interfaces:**
- Produces: phase-two pass report and evidence inventory.

- [ ] **Step 1: Run phase-two service tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_evidence_cache.py tests/test_v12_research_signals.py tests/test_v12_frontier_discovery.py tests/test_v12_frontier_quality.py tests/test_v12_journal_discovery.py tests/test_v12_journal_selection.py -q
```

- [ ] **Step 2: Run phase-two UI tests offscreen**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_frontier_ui.py tests/test_v12_journal_selection_ui.py tests/test_theme_settings_v11.py tests/test_v115_cross_page_ui.py -q
```

- [ ] **Step 3: Run all existing frontier, journal and selection regressions**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_frontier_scoring_v11.py tests/test_frontier_service_v11_integration.py tests/test_frontier_library_and_health_v11.py tests/test_journal_library_v11.py tests/test_journal_library_easyscholar_v111.py tests/test_journal_quality_v111.py tests/test_v1155_selection_gate.py tests/test_v1156_journal_actions.py -q
```

- [ ] **Step 4: Run deterministic source failure matrix**

For each source, simulate timeout, invalid JSON, rate limit and empty result while the remaining sources succeed. Assert UI completion, cached fallback and source-status evidence.

- [ ] **Step 5: Run EasyScholar configured/unconfigured matrix**

Use redacted fixtures. Confirm configured mode admits only verified Q1/Q2 to the journal feed and enforces selected divisions in selection; unconfigured mode performs no division elimination.

- [ ] **Step 6: Review screenshots and geometry reports**

Reject for abnormal font size, clipped quartile text, hidden controls, card nesting, blank progress or result action mismatch.

- [ ] **Step 7: Finalize the phase report**

Record exact pass counts, source matrix, quality matrix, screenshot inventory and residual network risks. State that no installer was built and no formal data was touched.

- [ ] **Step 8: Advance only after the report passes**

Begin `docs/plans/2026-08-31-v12-phase3-special-issues.md` only after every phase-two gate is green.
