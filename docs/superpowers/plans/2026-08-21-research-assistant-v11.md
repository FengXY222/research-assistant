# 科研助手 v11.0.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver 科研助手 v11.0.0 as a data-safe Windows upgrade that retains the familiar desktop-widget layout and all existing business capabilities, while reorganising seven legacy pages into four workbenches, introducing a clean six-theme visual system, and completing the explainable AI research-profile, daily-frontier, journal-library, and journal-selection workflows.

**Architecture:** Preserve the existing PySide6 + local atomic-JSON application. Add small, pure service modules for profile migration, frontier scoring, journal health and journal selection; keep `file_manager.py` as the compatibility and persistence boundary. Rebuild the UI shell around four workbenches while adapting the existing pages instead of replacing their data model. DeepSeek remains an optional asynchronous advisor: every AI result is schema-validated, explainable, user-confirmed where it would write data, and has a deterministic local fallback.

**Tech Stack:** Python 3 (`S:\Python\Scripts\python.exe`), PySide6, standard-library `unittest`, pypdf, Windows QLocalServer single-instance support, existing DeepSeek HTTP integration, Inno Setup, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-08-21-research-assistant-v11-design.md`

## Global Constraints

- Preserve all existing JSON files and fields, including `papers -> journals[]`, rejection archive, timeline entries, achievements and linked PDF paths. New fields are optional and normalizers must retain unknown legacy fields.
- Never point a test, migration, backup or installer probe at the formal data directory. Tests use a temporary data root injected before importing `utils.file_manager`.
- Do not use a database, server, account, cloud sync, or automatic external write. Network access is limited to already configured public metadata/JCR services and an explicitly configured DeepSeek key.
- Use `save_*` APIs and the existing atomic `_write_json`; do not add direct JSON writes in page classes.
- In the absence of a Git repository, do not invent commits. At each task boundary record verified changed files and commands in the task checkbox plus the v11 self-check report.
- Keep AI-derived information visibly distinct from user data and Clarivate/manual JCR data. AI estimates cannot overwrite verified or manually locked values.
- Default theme is 雾青蓝. No gradients, glow effects, unreadable transparent cards, hard-coded page-level theme palettes, or high-contrast white blocks in the dark theme.
- All AI calls run off the UI thread. If the provider, network, JSON, or parsing fails, the local workflow remains usable and no user data is overwritten.

## Test Environment Convention

Create `tests/_data_root.py` before importing application persistence modules:

```python
import os
import tempfile
from pathlib import Path

TEST_ROOT = tempfile.TemporaryDirectory()
os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(Path(TEST_ROOT.name) / "data")
```

Add a supported `RESEARCH_ASSISTANT_DATA_DIR` override in `utils/file_manager.py` so all test data is isolated. The formal runtime continues to resolve data next to the formal installation unless the user has deliberately selected another local data directory.

---

## Task 1 — Establish a v11 test harness and a non-mutating data boundary

**Files:**
- Modify: `utils/file_manager.py`
- Create: `tests/__init__.py`
- Create: `tests/_data_root.py`
- Create: `tests/test_data_root_and_legacy_safety.py`
- Create: `tools/self_check_v110.py`

- [x] **Step 1: Write failing isolation and preservation tests.**

```python
# tests/test_data_root_and_legacy_safety.py
from pathlib import Path
from unittest import TestCase
from utils import file_manager

class DataRootTests(TestCase):
    def test_explicit_test_root_never_equals_formal_install_data(self):
        self.assertIn("data", str(file_manager.data_location()).casefold())
        self.assertNotEqual(
            Path(file_manager.data_location()).resolve(),
            Path(r"C:\Users\fxy17\AppData\Local\Programs\科研助手\data").resolve(),
        )

    def test_normalizing_a_paper_preserves_unknown_legacy_fields(self):
        paper = file_manager.normalize_paper({"title": "x", "legacy_marker": {"keep": True}})
        self.assertEqual(paper["legacy_marker"], {"keep": True})
```

- [x] **Step 2: Implement the environment override and lossless normalizer rule.**
  - Resolve data root in one function at the top of `utils/file_manager.py`; accept `RESEARCH_ASSISTANT_DATA_DIR` only when non-empty and local.
  - Audit `_normalize_*` functions used by v11 (`_normalize_frontier_profile`, `_normalize_frontier_item`, `normalize_library_journal`, `normalize_paper`, `normalize_achievement`) so they start from `dict(raw)` and then normalise owned fields. Do not discard unknown fields.
  - Keep runtime default behavior unchanged for the formal install path and settings-managed custom data location.

- [x] **Step 3: Add a CLI self-check.**
  - `tools/self_check_v110.py` must accept `--data-root <path>` and `--read-only`.
  - In read-only mode, report record counts, referenced PDF-path existence count, duplicate IDs, future-date violations, malformed JCR states and SHA-256 hashes without saving files.
  - In temporary-copy mode, load/save every existing data file and assert semantic counts, IDs and unknown fields survive.

- [x] **Step 4: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_data_root_and_legacy_safety -v
& 'S:\Python\Scripts\python.exe' 'S:\小软件\tools\self_check_v110.py' --data-root 'C:\Users\fxy17\AppData\Local\Programs\科研助手\data' --read-only
```

**Acceptance:** Tests never write to formal data; the read-only report lists existing data without mutating it; unknown fields round-trip.

## Task 2 — Introduce the v11 settings schema and dual-window profiles

**Files:**
- Modify: `utils/file_manager.py`
- Modify: `ui/main_window.py`
- Create: `tests/test_v11_settings_migration.py`

- [ ] **Step 1: Write failing settings migration tests.**

```python
class V11SettingsTests(TestCase):
    def test_old_settings_gain_safe_v11_defaults(self):
        normalized = normalize_app_settings({"opacity": 96, "nav_order": ["home", "todo"]})
        self.assertEqual(normalized["appearance"]["theme_id"], "fog_teal")
        self.assertEqual(normalized["application_mode"], "widget")
        self.assertEqual(normalized["workbench_order"], ["home", "work", "papers", "library"])
        self.assertTrue(normalized["widget_window"]["width"] >= 400)

    def test_widget_and_software_geometry_are_independent(self):
        normalized = normalize_app_settings({"window": {"width": 520, "height": 680}})
        self.assertEqual(normalized["widget_window"]["width"], 520)
        self.assertGreaterEqual(normalized["software_window"]["width"], 920)
```

- [ ] **Step 2: Refactor settings normalization into explicit public helpers.**
  - Add `normalize_app_settings(payload: Any) -> dict[str, Any]`; make both `load_app_settings()` and `save_app_settings()` use it.
  - Add defaults without removing legacy settings:

```python
"appearance": {"theme_id": "fog_teal", "density": "comfortable"},
"application_mode": "widget",
"workbench_order": ["home", "work", "papers", "library"],
"widget_window": {"x": None, "y": None, "width": 520, "height": 680, "locked": False},
"software_window": {"x": None, "y": None, "width": 1180, "height": 820, "maximized": False},
"research": {"daily_profile_update": True, "filter_known_q3_q4": True},
```

  - Populate `widget_window` from the old `window` once, retain `window` on disk for backwards compatibility, and synchronise both only where needed.
  - Keep existing sidebar, click-through, auto-start, hotkey, backup and AI/JCR settings intact.

- [ ] **Step 3: Add application-mode geometry handling in `MainWindow`.**
  - Create `_apply_application_mode(mode, persist=True)`, `_capture_mode_geometry()`, and `_restore_mode_geometry()`.
  - Widget mode retains the frameless `Tool` semantics, tray behaviour, sidebar collapse and click-through.
  - Software mode uses a normal resizable application window, no automatic tray hide on launch, an appropriate taskbar entry, 920×680 minimum and 1180×820 default; it does not erase widget geometry.
  - When switching back to widget mode, restore the saved widget geometry exactly; ensure content-collapse never persists the strip width as full widget width.

- [ ] **Step 4: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_v11_settings_migration -v
& 'S:\Python\Scripts\python.exe' -m compileall 'S:\小软件\main.py' 'S:\小软件\ui' 'S:\小软件\utils'
```

**Acceptance:** Existing settings load without data loss; widget and software geometry persist independently; old navigation arrays do not control the new four-workbench navigation.

## Task 3 — Build a lossless, weighted research-profile v11 service

**Files:**
- Create: `utils/research_profile_service.py`
- Modify: `utils/file_manager.py`
- Create: `tests/test_research_profile_v11.py`

- [ ] **Step 1: Write failing profile migration and reconciliation tests.**

```python
class ResearchProfileV11Tests(TestCase):
    def test_legacy_terms_are_migrated_without_deleting_legacy_fields(self):
        profile = normalize_research_profile_v11({
            "primary_keywords": ["SOC"],
            "secondary_keywords": ["MAOC"],
            "feedback": {"term_weights": {"soc": 2}},
        })
        weights = {term["text"].casefold(): term["weight"] for term in profile["terms"]}
        self.assertEqual(weights["soc"], 72)
        self.assertEqual(weights["maoc"], 45)
        self.assertEqual(profile["primary_keywords"], ["SOC"])

    def test_locked_term_wins_over_ai_proposal_and_exclusion_wins_over_all(self):
        profile = normalize_research_profile_v11({"terms": [{"text": "SOC", "weight": 100, "locked": True}]})
        revised, log = reconcile_profile_proposal(profile, {
            "terms": [{"text": "SOC", "weight": 20}, {"text": "marine sediment", "weight": 90}],
            "excluded_terms": ["marine sediment"],
        }, today="2026-08-21")
        self.assertEqual(revised["terms"][0]["weight"], 100)
        self.assertNotIn("marine sediment", [x["text"].casefold() for x in revised["terms"]])
        self.assertTrue(any(entry["kind"] == "conflict" for entry in log))
```

- [ ] **Step 2: Implement pure profile functions.**
  - `normalize_research_profile_v11(raw) -> dict`: normalise `{terms, excluded_terms, pending_terms, update_log, filter_known_q3_q4}` while retaining every legacy profile field.
  - Canonicalise comparison keys case-insensitively and whitespace-insensitively; preserve the user-facing text of the first retained term.
  - `lock_term(profile, term_id)`, `unlock_term(profile, term_id)`, `remove_term(profile, term_id)`, and `set_excluded_terms(profile, terms)` must be deterministic and unit-testable.
  - `reconcile_profile_proposal(profile, proposal, today)` must preserve locked terms at 100, reject conflicting or evidence-free automatic additions into `pending_terms`, limit AI weights to 1–99, append an auditable change record and avoid mutually contradictory active/excluded terms.
  - Add `profile_source_signature(achievements, feedback, settings)` that incorporates linked PDF fingerprints only, never raw PDF content.

- [ ] **Step 3: Make persistence adopt v11 safely.**
  - Replace the owned part of `_normalize_frontier_profile` with `normalize_research_profile_v11` while preserving `primary_keywords`, `secondary_keywords`, feedback weights, source configuration, daily limit and other legacy fields for fallback.
  - New profile fields must remain optional so a partially written or pre-v11 `frontier.json` still loads.

- [ ] **Step 4: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_research_profile_v11 -v
```

**Acceptance:** Old primary/secondary terms map to 70/45 plus bounded legacy feedback; locked terms are immutable; excluded terms never participate in active matching; no legacy fields are deleted.

## Task 4 — Replace duplicate frontier ranking with one explainable v11 scorer

**Files:**
- Create: `utils/frontier_scoring.py`
- Modify: `utils/frontier_service.py`
- Modify: `utils/file_manager.py`
- Create: `tests/test_frontier_scoring_v11.py`

- [ ] **Step 1: Write failing scoring and quality-filter tests.**

```python
class FrontierScoringTests(TestCase):
    def test_total_score_uses_weighted_terms_and_fixed_quality_rules(self):
        result = evaluate_frontier_candidate(
            item={"title": "SOC MAOC modelling", "journal": "CATENA", "author_keywords": ["SOC", "MAOC"]},
            profile={"terms": [
                {"text": "SOC", "weight": 80}, {"text": "MAOC", "weight": 60}
            ], "filter_known_q3_q4": True},
            journal={"frontier_priority": "必看", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}},
        )
        self.assertEqual(result["score"], 240)  # 80 + 60 + 50 + 50
        self.assertEqual(result["score_breakdown"]["quality"], 50)

    def test_known_q3_is_hidden_only_when_filter_is_on(self):
        item = {"title": "SOC MAOC", "journal": "Example", "author_keywords": ["SOC", "MAOC"]}
        profile = {"terms": [{"text": "SOC", "weight": 70}, {"text": "MAOC", "weight": 45}], "filter_known_q3_q4": True}
        journal = {"frontier_priority": "扩展", "jcr": {"status": "verified", "metrics": [{"quartile": "Q3"}]}}
        self.assertIsNone(evaluate_frontier_candidate(item, profile, journal, {}))
        profile["filter_known_q3_q4"] = False
        self.assertIsNotNone(evaluate_frontier_candidate(item, profile, journal, {}))

    def test_unknown_jcr_is_not_blocked_or_penalized(self):
        item = {"title": "SOC MAOC", "journal": "Unknown", "author_keywords": ["SOC", "MAOC"]}
        profile = {"terms": [{"text": "SOC", "weight": 70}, {"text": "MAOC", "weight": 45}], "filter_known_q3_q4": True}
        result = evaluate_frontier_candidate(item, profile, {"frontier_priority": "扩展", "jcr": {"status": "pending"}}, {})
        self.assertEqual(result["score_breakdown"]["quality"], 0)
        self.assertEqual(result["jcr_state"], "未知")

    def test_quality_feedback_does_not_reduce_topic_weights(self):
        classified = classify_feedback_locally("这个期刊三四区，不适合投")
        self.assertEqual(classified["kind"], "journal_quality")
        self.assertEqual(classified["term_weight_delta"], 0)
```

- [ ] **Step 2: Create the single source of truth for matching and scoring.**
  - Move final matching, deduplication, journal lookup and daily selection to `utils/frontier_scoring.py`; remove all duplicate redefinitions from `utils/frontier_service.py` and leave only public wrappers/import compatibility where necessary.
  - Implement `evaluate_frontier_candidate(item, profile, journal_index, feedback_index) -> dict | None`.
  - Require at least two *distinct* active profile-term matches. Prefer author/source keywords, then title/abstract fallback; record `match_source`, `matched_term_ids`, and `matched_terms`.
  - Implement exactly:

```text
term score = sum(matched active term weights)
priority = 必看 50 / 关注 35 / 扩展 15 / otherwise 0
verified JCR = Q1 50 / Q2 20 / Q3,Q4,unknown 0
feedback = clamp(-30, +30)
AI = clamp(-15, +15)
total = all five components summed
```

  - Store the named `score_breakdown` map on each item. Do not use old `primary_pair`, `mixed_pair`, `secondary_pair`, `strict` or `explore` as ranking inputs; retain them only as imported historical display metadata.
  - Filter only *verified/manual known* Q3/Q4 and explicit `journal_quality` feedback when `filter_known_q3_q4` is true. AI JCR estimates remain visible as “待核验” and never hard-filter.
  - `select_daily_recommendations()` must sort by total score, then publication date, then stable ID; deduplicate DOI/title journal pairs across old items.

- [ ] **Step 3: Preserve and migrate item data.**
  - Extend `_normalize_frontier_item` with `score_breakdown`, `journal_quality_flag`, `feedback_events`, `one_line_feedback`, `library_journal_id` and `profile_algorithm_version`.
  - Retain old `relevance_level`, `ai_score`, `feedback`, `status` and field names for rendering old data; migrate scoring only when refresh or cache repair runs.

- [ ] **Step 4: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_frontier_scoring_v11 -v
& 'S:\Python\Scripts\python.exe' -m compileall 'S:\小软件\utils\frontier_service.py' 'S:\小软件\utils\frontier_scoring.py'
```

**Acceptance:** Every v11 card can explain its single total; known Q3/Q4 default filtering, unknown-JCR inclusion, and quality-only feedback are deterministic; the obsolete pairing scheme cannot affect ranking.

## Task 5 — Harden the DeepSeek contracts for profile learning, frontier feedback and journal selection

**Files:**
- Modify: `utils/ai_service.py`
- Create: `tests/test_ai_contracts_v11.py`

- [ ] **Step 1: Write mocked provider tests.**

```python
class AiContractsTests(TestCase):
    def test_malformed_json_returns_local_safe_failure_without_mutation(self):
        with patch("utils.ai_service._post_deepseek", return_value={"choices": []}):
            with self.assertRaises(DeepSeekRequestError):
                request_profile_proposal({}, [], [], lambda _message: None)

    def test_profile_contract_rejects_weight_100_for_unlocked_ai_term(self):
        with self.assertRaises(ValueError):
            validate_profile_proposal({"terms": [{"text": "SOC", "weight": 100, "evidence": []}]})

    def test_journal_rerank_is_limited_to_minus_ten_to_plus_ten(self):
        self.assertEqual(validate_selection_ai_patch({"adjustment": 80})["adjustment"], 10)
```

- [ ] **Step 2: Keep `_chat_json` as the safe transport and add validated domain contracts.**
  - Do not scatter new HTTP code. Extend `_chat_json` to use a schema name in prompts, field-whitelist normalisation, output length bounds, a single safe retry for malformed provider output, and error messages suitable for UI status text.
  - Add pure validators and dedicated functions:

```python
def request_profile_proposal(profile: dict, achievements: list[dict], feedback_items: list[dict], progress) -> dict: pass
def classify_frontier_feedback_with_ai(text: str, context: dict) -> dict: pass
def rerank_frontier_with_ai(profile: dict, items: list[dict]) -> dict: pass
def rank_journals_with_ai(paper: dict, candidates: list[dict], profile: dict) -> dict: pass
def propose_journal_candidates_with_ai(paper: dict, profile: dict) -> dict: pass
```

  - Profile output supports terms, exclusions, evidence summaries, confidence, suggested search phrases and conflicts. Validators reject manual/locked replacement instructions and evidence-free new active terms.
  - Feedback output supports `topic_positive`, `topic_negative`, `method_or_object_preference`, `journal_quality`, `reading_value`, `uncertain`; enforce bounded weight changes and metadata only.
  - Selection output supports candidate IDs only for existing candidates, `adjustment` -10..+10, an AI submission hint and risks. Candidate proposals return a `pending_candidates` array only; they are never saved or auto-added.

- [ ] **Step 3: Preserve existing UI fallbacks.**
  - Existing `refine_research_profile_with_ai`, `rerank_frontier_with_ai` and `rank_journals_with_ai` become compatibility wrappers or are adapted to call validated v11 functions.
  - Callers must catch `DeepSeekConfigurationError` and `DeepSeekRequestError`, leave current local scoring on screen, and never write a partial AI patch.

- [ ] **Step 4: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_ai_contracts_v11 -v
```

**Acceptance:** The existing “unrecognisable structured result” failure becomes a clear, recoverable UI state; malformed/oversized model output cannot corrupt local data or make an essential feature unavailable.

## Task 6 — Add auditable feedback events, frontier-to-library import and journal health state

**Files:**
- Modify: `utils/file_manager.py`
- Create: `utils/journal_health_service.py`
- Create: `tests/test_frontier_library_and_health_v11.py`

- [ ] **Step 1: Write failing persistence tests.**

```python
class FrontierLibraryTests(TestCase):
    def test_frontier_journal_import_is_name_publisher_deduplicated(self):
        imported, created = import_frontier_journal([], {"journal": "CATENA", "publisher": "Elsevier", "id": "f1"})
        again, created_again = import_frontier_journal(imported, {"journal": "catena", "publisher": "ELSEVIER", "id": "f2"})
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["frontier_priority"], "扩展")

    def test_verified_jcr_is_never_overwritten_by_ai_health_patch(self):
        journal = {"name": "CATENA", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}}
        updated = merge_journal_health_patch(journal, {"jcr": {"status": "ai_estimated", "metrics": [{"quartile": "Q4"}]}})
        self.assertEqual(updated["jcr"]["status"], "verified")
        self.assertEqual(updated["jcr"]["metrics"][0]["quartile"], "Q1")

    def test_health_check_targets_only_changed_or_incomplete_records(self):
        journals = [
            {"id": "complete", "name": "A", "issn": "1", "website": "https://a.test", "metadata_source_signature": "same"},
            {"id": "incomplete", "name": "B", "issn": "", "website": "", "metadata_source_signature": ""},
        ]
        targets = journal_health_targets(journals, model="deepseek-chat", today="2026-08-21")
        self.assertEqual([target["id"] for target in targets], ["incomplete"])
```

- [ ] **Step 2: Add lossless data fields and pure services.**
  - Add `record_frontier_feedback_event(data, item_id, action, text, classification, now)`; persist action, source item ID, journal, matched terms, score snapshot, timestamp and text.
  - Add `import_frontier_journal(library, frontier_item, now) -> tuple[list[dict], bool]` using `_journal_key`/canonical publisher; create only a minimum record with `frontier_priority: "扩展"`, `first_discovered_from: "frontier"`, source URL and date.
  - Extend `normalize_library_journal` with `provenance`, `health`, `user_quality_flag`, `jcr_locked` and source signatures without removing current fields.
  - Implement `journal_health_status(journal, today)`, `journal_health_targets(library, model, today)` and `merge_journal_health_patch(journal, patch)` returning explicit reasons: `verified`, `ai_pending_verification`, `incomplete`, `needs_metadata_update`, `user_low_quality`.
  - Preserve manual JCR and verified JCR; only patch AI estimates when neither has priority and the record is not locked.

- [ ] **Step 3: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_frontier_library_and_health_v11 -v
```

**Acceptance:** One-click import is idempotent; feedback is auditable; health checks are incremental; verified/manual JCR survives all AI updates.

## Task 7 — Rebuild 每日前沿 and research-profile UI around evidence, feedback and compact cards

**Files:**
- Modify: `ui/frontier_page.py`
- Create: `ui/research_profile_dialog.py`
- Modify: `ui/dialogs.py`
- Create: `tests/test_frontier_ui_contract_v11.py`

- [ ] **Step 1: Write UI-contract tests for non-network behavior.**
  - Test profile drawer/dialect values can render an old profile and a v11 profile without an exception.
  - Test a `FrontierCard` with unknown JCR renders a visible “分区未知” badge rather than a blocking warning.
  - Test an item classified `journal_quality` changes only journal quality feedback, not profile term weights.

- [ ] **Step 2: Replace the crowded card with a progressive-disclosure card.**
  - Keep only title, journal/source, JCR state, total score and one line “为什么推荐” in the collapsed card.
  - Add a compact details expander containing score breakdown, matched terms, author/abstract source, AI rationale and risks. Never show old “一级×2” language.
  - Fixed actions: `不相关`, `加入待读`, `标为灵感`, `一句话评价`, `加入期刊库`/`已在库`. One-line feedback expands inline beneath the current card and saves only after explicit submit.
  - Add `FrontierCard.feedback_requested` and `import_journal_requested` signals; keep original/related/read/restore behavior only where it has a clear v11 mapping.

- [ ] **Step 3: Add the research-profile drawer/dialog.**
  - `ResearchProfileDialog` shows active terms as weight bars, lock state, evidence summary, excluded terms, pending terms, latest update log and source freshness.
  - Users can lock/unlock, remove, exclude and accept/reject pending terms; normal active-term numeric editing is intentionally absent.
  - Provide the `已知 Q3/Q4 过滤` checkbox and a readable explanation that unknown quartiles are not filtered.
  - Run profile updates in `QThread`; compare source signature before calling DeepSeek, show which evidence changed, and allow a no-API “local refresh” path.

- [ ] **Step 4: Update page state transitions.**
  - Replace `_adjust_feedback` with event-based learning that follows Task 5 classification. Quality feedback updates journal state/filters only.
  - Update `_visible_items`, `_brief` and `select_daily_recommendations` use to read v11 total score and filtering results.
  - Connect imported journal success to `open_journal_library` with a notice, not a forced navigation.

- [ ] **Step 5: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_frontier_ui_contract_v11 -v
```

**Acceptance:** A new user understands a frontier card without inspecting internals; one-line feedback cannot silently change the wrong kind of preference; the full profile remains accessible on narrow widgets without crushing article text.

## Task 8 — Make the journal library compact, health-aware and safely enrichable

**Files:**
- Modify: `ui/journal_library_page.py`
- Modify: `utils/journal_service.py`
- Modify: `utils/jcr_service.py`
- Create: `tests/test_journal_library_v11.py`

- [ ] **Step 1: Write layout/data behavior tests.**
  - Test group ordering uses canonical publisher family, case-insensitively.
  - Test a Journal row model exposes name, abbreviated publisher, truncated tags, JCR source/state, and a compact more-action list.
  - Test metadata/JCR/AI target calculation skips unchanged entries and reports a reason for every target.

- [ ] **Step 2: Rework list rows and grouping.**
  - Keep the journal name in a dedicated first column at no less than 50% of list width; use elided publisher/tags in secondary columns.
  - Replace four persistent row buttons with hover/reveal `编辑` and `更多`; put low-frequency `删除`, `收藏`, `导出`, and `资料更新` in the menu.
  - Retain explicit move/reorder affordance only when manual sorting is selected. In publisher grouping mode, journal row movement must not suggest it can override group ordering.
  - Group by canonical parent publisher and render short, compact headers without large blank vertical gaps.

- [ ] **Step 3: Make health/JCR state legible and incremental.**
  - Show exactly one small state chip: `已核验 Q1/Q2/Q3/Q4`, `AI 估计·待核验`, `资料不全`, `待更新`, `手动`, or `未知`.
  - Expose a click-through detail panel/dialog with JCR source, checked date, confidence, category/metric and the “AI does not equal Clarivate” warning.
  - “全部更新” must use `journal_health_targets`; it should state how many were skipped as unchanged before any network call.
  - DeepSeek metadata completion may estimate JCR as clearly non-authoritative, but cannot override manual/verified data and should batch/retry safely.

- [ ] **Step 4: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_journal_library_v11 -v
```

**Acceptance:** The library is readable at widget width, especially long journal names; update actions are understandable, non-redundant and API-efficient; all JCR provenance is visible.

## Task 9 — Implement the explainable AI journal-selection workbench

**Files:**
- Create: `utils/journal_selection_service.py`
- Create: `ui/journal_selection_dialog.py`
- Modify: `ui/journal_library_page.py`
- Modify: `ui/paper_page.py`
- Create: `tests/test_journal_selection_v11.py`

- [ ] **Step 1: Write failing local-score, fallback and submission-path tests.**

```python
class JournalSelectionTests(TestCase):
    def test_local_components_are_bounded_and_total_is_explainable(self):
        score = score_journal_candidate(paper, journal, profile, history, constraints={})
        self.assertLessEqual(score["topic_fit"], 40)
        self.assertLessEqual(score["quality"], 25)
        self.assertLessEqual(score["personal_experience"], 20)
        self.assertLessEqual(score["constraints"], 15)
        self.assertEqual(score["local_score"], sum(score[key] for key in ("topic_fit", "quality", "personal_experience", "constraints")))

    def test_ai_failure_keeps_local_order(self):
        candidates = rank_journal_candidates(paper, library, profile, history={}, constraints={})
        revised = apply_ai_selection_patch(candidates, {"ranked": []})
        self.assertEqual([row["journal_id"] for row in revised], [row["journal_id"] for row in candidates])

    def test_add_to_submission_path_appends_one_journal_without_touching_history(self):
        paper = {"id": "p1", "title": "SOC", "journals": [{"id": "old", "name": "Old Journal", "status": "拒稿"}]}
        updated = append_journal_to_submission_path(paper, {"id": "new", "name": "CATENA", "publisher": "Elsevier"}, today="2026-08-21")
        self.assertEqual(len(updated["journals"]), 2)
        self.assertEqual(updated["journals"][0]["id"], "old")
        self.assertEqual(updated["journals"][1]["status"], "准备投稿")
```

- [ ] **Step 2: Extract the local selection engine from the old picker.**
  - Move `JournalPickerDialog._score`, filtering and ranking out of `ui/journal_library_page.py` into pure functions: `rank_journal_candidates`, `apply_ai_selection_patch` and `append_journal_to_submission_path`.
  - Define `score_journal_candidate(paper, journal, profile, usage, constraints) -> dict` with exactly:

```text
topic_fit: 0..40
quality: 0..25
personal_experience: 0..20
constraints: 0..15
local_score: 0..100
ai_adjustment: -10..+10
total_score: local_score + ai_adjustment
```

  - Make constraints explicit and user-editable: known-Q3/Q4 policy, language/field mismatch, previously rejected for same manuscript, required OA/risk notes, personal `不适合` feedback. AI cannot override hard exclusions.
  - Return named reasons per component and `needs_verification` risks; never label total score as acceptance probability.

- [ ] **Step 3: Build `JournalSelectionDialog`.**
  - Open in the current paper context; left candidate list is wide enough for full/preferred journal names, right detail pane contains why it fits, four score rows, local + AI = total equation, submission advice, risks, open-library/edit path and `加入投稿路径`.
  - Provide filter controls for priority/JCR/status and a `显示已排除` diagnostic view. Default respects known Q3/Q4 filter.
  - `AI 复核` only sends paper title/abstract/keywords, profile and current shortlist after user click; no achievement PDF or full submission notes are sent.
  - AI candidate proposals show in a separate “待审候选” section and require explicit `加入期刊库` confirmation.

- [ ] **Step 4: Integrate one safe write path.**
  - Replace `JournalPickerDialog` usage in `JournalLibraryPage._choose_for_paper` and `PaperPage` with the new dialog.
  - `加入投稿路径` creates exactly one new `journals[]` entry with `准备投稿`, today for `date` and `status_updated_at`, and a first timeline event. It must deduplicate per paper while preserving all existing entries, even same-named journals in different papers.

- [ ] **Step 5: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_journal_selection_v11 -v
```

**Acceptance:** Selection works without DeepSeek; score arithmetic is visible and bounded; AI cannot fabricate a verified JCR result; creating a target journal never corrupts multi-journal history.

## Task 10 — Recompose legacy pages into four workbenches without losing entry points

**Files:**
- Create: `ui/workbench_shell.py`
- Create: `ui/quick_capture_dialog.py`
- Modify: `ui/main_window.py`
- Modify: `ui/home_page.py`
- Modify: `ui/todo_page.py`
- Modify: `ui/notes_page.py`
- Modify: `ui/paper_page.py`
- Modify: `ui/achievements_page.py`
- Create: `tests/test_workbench_routing_v11.py`

- [ ] **Step 1: Write routing and quick-capture tests.**

```python
class WorkbenchRoutingTests(TestCase):
    def test_legacy_routes_resolve_to_one_of_four_workbenches(self):
        self.assertEqual(resolve_route("todo").workbench, "work")
        self.assertEqual(resolve_route("notes").workbench, "work")
        self.assertEqual(resolve_route("achievements").workbench, "papers")
        self.assertEqual(resolve_route("frontier").workbench, "library")

    def test_quick_capture_requires_confirmation_before_write(self):
        draft = classify_quick_capture_locally("查一下 2025 年遥感秸秆研究")
        self.assertEqual(draft["kind"], "inspiration")
        self.assertFalse(draft["confirmed"])
```

- [ ] **Step 2: Create the routing registry.**
  - Define four primary routes `home`, `work`, `papers`, `library` and aliases:

```python
ROUTE_ALIASES = {
    "todo": ("work", "tasks"), "notes": ("work", "notes"),
    "papers": ("papers", "submissions"), "achievements": ("papers", "results"),
    "journals": ("library", "journals"), "frontier": ("library", "frontier"),
}
```

  - Existing external calls (`open_todo`, tray actions, global journal import, HOME cards, reminder targets) must use `navigate(route, anchor=None)` and keep working.

- [ ] **Step 3: Build the two shells.**
  - `WorkbenchShell` in widget mode uses the four existing familiar vertical edge labels and preserves the HOME card geometry.
  - In software mode it creates a continuous `QScrollArea` with top anchor chips and four sections. Embed/adapt existing task, notes, paper, achievement, journal and frontier panels as section content; do not duplicate data or create a second state model.
  - Use adaptive grid/stack layouts: 380/460/540 px widget widths and 920/1180/1366 px software widths must reflow without horizontal scroll for content other than intentional text/detail panes.

- [ ] **Step 4: Add “今日下一步” and unified quick capture.**
  - Add a HOME card limited to three actions sourced from existing todos, revision due dates, overdue/pending reminders and long-running active journal entries. Each result includes a short reason and navigates to the existing record.
  - Add one compact `＋` quick-capture entry. It may propose task/inspiration/reading/candidate journal with deterministic cues first, optional AI classification second, and a review form before any `save_*` call.
  - Keep current todo scheduling, history, migration, inspiration editing, reading status and achievement/PDF flows available under their new anchors.

- [ ] **Step 5: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_workbench_routing_v11 -v
```

**Acceptance:** There are only four primary navigation labels; all legacy user flows still resolve to their content; neither widget nor software mode creates duplicate data or hidden dead-end pages.

## Task 11 — Install a semantic six-theme UI system and simplify settings

**Files:**
- Replace: `ui/theme.py`
- Modify: `ui/main_window.py`
- Modify: `ui/home_page.py`
- Modify: `ui/todo_page.py`
- Modify: `ui/paper_page.py`
- Modify: `ui/notes_page.py`
- Modify: `ui/journal_library_page.py`
- Modify: `ui/frontier_page.py`
- Modify: `ui/achievements_page.py`
- Modify: `ui/settings_dialog.py`
- Modify: `ui/dialogs.py`
- Modify: `ui/workflow_dialogs.py`
- Modify: `ui/reminder_dialog.py`
- Modify: `ui/intelligence_dialog.py`
- Modify: `ui/reorder.py`
- Create: `tests/test_theme_contract_v11.py`

- [ ] **Step 1: Write palette and settings-preview tests.**
  - Test `get_theme("fog_teal")` has the exact specified semantic colors and all six theme IDs have every required token.
  - Test a preview update changes application QSS but `cancel` restores the prior theme and opacity without saving settings.
  - Test all standard combo/drop-down/popup rules use theme tokens rather than a legacy `#101a36` literal.

- [ ] **Step 2: Implement a semantic palette registry.**

```python
THEMES = {
    "fog_teal": Theme("雾青蓝", base="#EAF2F2", surface="#F8FBFA", text="#173337",
                      accent="#246F79", success="#3B7560", warning="#A86D31", danger="#AA514D"),
    "ink_white": Theme("蓝墨白", base="#EDF3F8", surface="#FFFFFF", text="#152A3A", accent="#285B7A", success="#3F7A69", warning="#A86D31", danger="#A94C50"),
    "moss_paper": Theme("苔纸绿", base="#EEF2EA", surface="#FBFCF8", text="#263729", accent="#4C7657", success="#4E7B5B", warning="#956C34", danger="#A2564B"),
    "warm_sand": Theme("暖砂棕", base="#F5F0E7", surface="#FFFCF6", text="#413329", accent="#93643B", success="#5E7A5A", warning="#A96B2C", danger="#A45248"),
    "graphite_mist": Theme("石墨雾灰", base="#ECEFF0", surface="#FAFBFB", text="#2B3437", accent="#4E6870", success="#4E7464", warning="#93692E", danger="#A55252"),
    "night_sea": Theme("夜读深海", base="#0E1927", surface="#152337", text="#E8F1F5", accent="#68B8C5", success="#75C49A", warning="#E1AE66", danger="#E88989"),
}
def build_app_qss(theme: Theme, density: str) -> str: return _render_semantic_qss(theme, density)
```

  - Include tokens for base, surface, raised surface, text, muted text, border, accent, success, warning, danger, selection, input, menu, scrollbar and focus ring.
  - Apply global QSS at `QApplication` level and remove page/dialog-level color palettes in favor of object names and semantic selectors. Keep narrowly scoped size/layout QSS only where necessary.

- [ ] **Step 3: Rebuild settings into four readable sections.**
  - Sections: `外观`; `窗口与快捷方式`; `数据与备份`; `研究与智能`.
  - Use clear section summaries, word-wrapped labels, responsive `QFormLayout`, live theme/opacity preview and a single explicit save/cancel row.
  - Keep all current options: topmost, click-through, sidebar side/hide/collapse, widget lock, autostart, journal-import hotkey, global visibility hotkey, return-home idle, auto backup, data path/change/import/restore, DeepSeek and Clarivate settings, frontier/profile/journal background updates.
  - Add application mode selection in `外观` or `窗口与快捷方式` with a short behavior explanation.

- [ ] **Step 4: Run visual adapter pass across all UI.**
  - Replace hard-coded QSS colours in every listed page/dialog and ensure delete/archive confirmations, `QComboBox` popup, `QDateEdit`, scrollbars, menus, error banners and tooltips are fully themed.
  - Standardise compact cards, section headers, empty states, badges, hover actions, icon-only button tooltips and focus outlines.
  - Reduce permanent action-button noise: retain primary action in view; move low-frequency actions to context/hover menus.

- [ ] **Step 5: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_theme_contract_v11 -v
rg -n '#101a36|#111b2d|#202d4d|#edf4ff' 'S:\小软件\ui'
```

**Acceptance:** All six palettes are usable; 雾青蓝 is calm and readable; no legacy hard-coded palette remains except documented compatibility or generated tokens; settings no longer feels like an unstructured list.

## Task 12 — Preserve system integration: tray, keyboard, minimisation, reminders and startup

**Files:**
- Modify: `ui/main_window.py`
- Modify: `utils/global_hotkey.py`
- Modify: `utils/autostart.py`
- Create: `tests/test_system_integration_v11.py`

- [ ] **Step 1: Write non-destructive state tests and a manual smoke checklist.**
  - Unit-test shortcut config normalisation (`double_space`, custom sequence, off), mode-specific launch policy, and widget-geometry capture/restore helpers.
  - Add a manual smoke checklist in `tools/self_check_v110.py` for Windows-only effects that cannot safely be simulated in CI: global hotkey in another app, IME block, tray show/hide, close process, click-through unlock, autostart.

- [ ] **Step 2: Apply mode-specific behavior without regressing widget behavior.**
  - Widget mode: frameless Tool window, minimise-to-tray, close truly terminates process, global visibility shortcut works above other applications and IME guard remains on.
  - Software mode: conventional minimise/restore and visible taskbar entry; closing still exits fully; startup opens the application rather than silently hides it.
  - Ensure the background single-instance handler restores/activates the current instance rather than opening a duplicate window.
  - Keep double-Tab import configurable; update user-facing shortcut status messages and do not imply a shortcut is active when Windows registration failed.

- [ ] **Step 3: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest tests.test_system_integration_v11 -v
```

**Acceptance:** Existing desktop-widget behaviors remain reliable, software mode feels like a normal application, and hotkeys/tray/startup do not conflict with IME or duplicate-instance handling.

## Task 13 — Version, installer, user documentation and release integrity

**Files:**
- Modify: `utils/app_info.py`
- Modify: `version_info.txt`
- Modify: `科研助手.iss`
- Modify: `build_windows.ps1`
- Modify: `tools/build_user_manual.py`
- Create: `docs/科研助手-v11.0.0-使用说明.md`
- Create: `tools/verify_upgrade_v110.py`

- [ ] **Step 1: Set release identity.**
  - Set `APP_VERSION = "11.0.0"`; update Windows version resource, installer app version/display text, build artifact names and documentation references consistently.
  - Preserve the existing `INSTANCE_CHANNEL` unless a deliberate compatibility migration is separately tested; do not create a second running app/data channel by accident.

- [ ] **Step 2: Maintain formal installer safeguards.**
  - Confirm `UsePreviousAppDir=yes`, excludes for `data\*`, `uninsneveruninstall` (or equivalent preserved-data safeguards), and formal install-location detection remain present.
  - Add `verify_upgrade_v110.py` to: copy a test data snapshot, calculate JSON/PDF-reference hashes, execute installer verification steps only against an explicit temporary test install, then assert files/semantic records remain unchanged. It must refuse the formal install path unless `--allow-formal` is explicitly passed.
  - Do not bundle a default replacement `data` directory that overwrites current records.

- [ ] **Step 3: Write concise user documentation.**
  - Explain four workbenches, widget/software modes, themes, today-next-action, quick capture, multi-journal paper flow, rejection archive/result transfer, journal health/JCR provenance, AI journal selection, frontier feedback and research profile locks.
  - Add privacy/cost notes for DeepSeek, unknown-JCR and Q3/Q4 behavior, local backup/restore, data path, global shortcuts and troubleshooting.

- [ ] **Step 4: Verify.**

```powershell
& 'S:\Python\Scripts\python.exe' 'S:\小软件\tools\build_user_manual.py'
& 'S:\Python\Scripts\python.exe' 'S:\小软件\tools\verify_upgrade_v110.py' --help
```

**Acceptance:** Release metadata is uniformly 11.0.0; installation cannot silently create a parallel default data directory; the user can understand and recover all new workflows.

## Task 14 — First complete verification pass: automated, data-safety and visual structure

**Files:**
- Modify/Create: `tools/self_check_v110.py`
- Create: `docs/qa/v11-round-1-automated-and-visual.md`

- [ ] **Step 1: Execute the complete unit test suite and static compile.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest discover -s 'S:\小软件\tests' -v
& 'S:\Python\Scripts\python.exe' -m compileall 'S:\小软件\main.py' 'S:\小软件\ui' 'S:\小软件\utils'
```

- [ ] **Step 2: Run read-only data checks before any formal run.**

```powershell
& 'S:\Python\Scripts\python.exe' 'S:\小软件\tools\self_check_v110.py' --data-root 'C:\Users\fxy17\AppData\Local\Programs\科研助手\data' --read-only
```

  - Record input SHA-256 and record counts for all required JSON files, achievements with PDF links and malformed future dates.

- [ ] **Step 3: Visual sweep using a copied test data directory.**
  - Start the application in widget widths 380, 460 and 540 px; software widths 920, 1180 and 1366 px; then inspect HOME, WORK, PAPERS, LIBRARY, profile drawer, selection dialog, journal editor, paper editor, delete confirm, archive, results and settings under all six themes.
  - Record screenshots and results for text clipping, horizontal overflow, empty right-side space, inaccessible scrollbars, low-contrast menus, incorrect tooltip/action alignment and status colour mismatch.
  - Fix defects found here before proceeding to round 2; rerun affected tests after each fix.

- [ ] **Step 4: Write the report.**
  - `docs/qa/v11-round-1-automated-and-visual.md` contains commands, pass/fail output, responsive dimensions, screenshots, resolved defects, residual risks and explicit formal-data non-mutation proof.

**Acceptance:** All tests pass; no visual blockers at supported dimensions; formal data is unchanged.

## Task 15 — Second complete verification pass: real user workflows, installer upgrade and release build

**Files:**
- Create: `docs/qa/v11-round-2-real-flow-and-upgrade.md`
- Modify: release artifacts only after all checks pass

- [ ] **Step 1: Exercise the end-to-end workflow on a copy of the formal data.**
  1. Open with current papers, journals, achievements and PDF links.
  2. Add a one-sentence frontier evaluation; verify the correct feedback class and audit event.
  3. Import that frontier journal once; verify duplicate prevention.
  4. Trigger research-profile update with unchanged signature (no AI call), then with controlled new feedback/PDF signature (one call), and check locked/conflict handling.
  5. Run local selection and AI selection fallback; add a candidate to the paper’s submission path; verify all pre-existing `journals[]` remain.
  6. Change one journal status through reject/archive and one accepted/published transfer; verify the intended per-journal/per-paper rules still work.
  7. Toggle Q3/Q4 filtering and verify unknown/JCR/quality-feedback treatment.
  8. Switch themes, widget/software mode, sidebar side/hide, geometry lock, click-through, tray and global shortcut behavior.

- [ ] **Step 2: Build and test a default-path installer upgrade in a disposable test install.**
  - Build through `build_windows.ps1`.
  - Install a prior-version fixture at a temporary directory, then run v11 installer with default upgrade choices.
  - Compare before/after data hashes, record counts, linked PDF path strings, settings/window geometry and registry install location.
  - Confirm there is no second `data` directory created and that user-selected formal data path remains selected.

- [ ] **Step 3: Complete final verification and report.**

```powershell
& 'S:\Python\Scripts\python.exe' -m unittest discover -s 'S:\小软件\tests' -v
& 'S:\Python\Scripts\python.exe' 'S:\小软件\tools\self_check_v110.py' --data-root '<temporary-upgrade-data>' --read-only
```

  - Document evidence, screenshots, build checksum, installer filename, known limitations and the exact formal-data backup location made before release.
  - Only after these checks pass, create the v11 installer and hand it off with the usage guide.

**Acceptance:** All major workflows feel coherent; an upgrade preserves the real data model; v11.0.0 is fit for release.

## Execution Notes

- Execute tasks in order. Tasks 3–6 establish pure data/business behavior before UI rewrites; tasks 7–11 consume those contracts; tasks 14–15 are mandatory gates rather than optional polish.
- If a task reveals a mismatch with the approved specification, stop at that task, record the evidence in the plan/report and request a design decision instead of silently broadening scope.
- Because the repository currently has no `.git` directory, use task checkboxes and QA reports as the implementation ledger. Do not claim commits that do not exist.
