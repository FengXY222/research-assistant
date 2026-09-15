# 科研助手 v11.1 精修 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不迁移数据库、不改变用户现有操作习惯的前提下，完成 v11.1 的主题性能、HOME/WORK/PAPERS 精修、AI 选刊与 EasyScholar 期刊数据更新。

**Architecture:** Qt 调色板承担主题切换，静态 QSS 只承担组件语义；HOME 深链、选刊候选和期刊指标均保存稳定 ID 与来源元数据。EasyScholar 是可选后台数据源，所有请求由缓存/签名控制，手动和 Clarivate 数据不会被覆盖。

**Tech Stack:** Python 3.12、PySide6、JSON、Windows DPAPI、urllib、pytest。

**Spec:** `docs/superpowers/specs/2026-08-21-research-assistant-v11.1-refinement-design.md`

## Global Constraints

- 不修改 `C:\Users\fxy17\AppData\Local\Programs\科研助手\data` 的任何真实数据；测试只用 `RESEARCH_ASSISTANT_DATA_DIR`。
- 不在日志、异常或 UI 提示中输出任何 API 密钥或请求 URL 中的密钥参数。
- 未知、冲突或 AI 推测的期刊信息必须被明确标记，不能作为“已核验”事实。
- 项目不是 Git 仓库；每个任务以 pytest 与截图/性能记录作为检查点，不创建伪提交。

---

### Task 1: 完成主题性能与首页/工作区精修

**Files:**
- Modify: `ui/theme.py`, `ui/settings_dialog.py`, `ui/main_window.py`, `ui/home_page.py`, `ui/todo_page.py`, `ui/paper_page.py`, `ui/workbench_shell.py`
- Test: `tests/test_v111_refinement_contracts.py`, `tests/test_theme_settings_v11.py`, `tests/test_home_next_actions_v11.py`

**Interfaces:**
- Produces: `apply_application_theme(...) -> bool`, `HomePage.open_paper_journal(str, str)`, `PaperPage.reveal_journal(str, str) -> bool`.

- [x] **Step 1: Write failing contracts for no-op themes, direct task detail, paper deep-link, priority picker, compact archive and frontier default.**
- [x] **Step 2: Run the contracts and confirm each original behavior fails.**
- [x] **Step 3: Implement minimal palette/no-op, HOME, WORK, PAPERS and routing changes.**
- [x] **Step 4: Run focused regression tests.**

### Task 2: Build safe EasyScholar data adapter

**Files:**
- Create: `utils/easyscholar_service.py`
- Modify: `utils/file_manager.py`, `ui/intelligence_dialog.py`, `ui/settings_dialog.py`, `ui/journal_library_page.py`, `utils/frontier_scoring.py`
- Test: `tests/test_easyscholar_service_v111.py`, `tests/test_frontier_scoring_v11.py`

**Interfaces:**
- Produces: `is_easyscholar_ready() -> bool`, `fetch_easyscholar_metrics(journal) -> dict`, `enrich_journals_with_easyscholar(journals) -> dict`.
- Consumes: `easyscholar` settings object with `enabled`, `secret_key_secret`, `cache_days`.

- [ ] **Step 1: Write mocked-response tests for parsing Q1/Q4, CAS fields, cache skipping, secret-safe errors and manual/Clarivate precedence.**
- [ ] **Step 2: Run tests and confirm the adapter import/function names are absent.**
- [ ] **Step 3: Implement the adapter and journal normalization fields; do not issue live requests in tests.**
- [ ] **Step 4: Add setting controls and library worker/menu integration; update daily frontier filtering.**
- [ ] **Step 5: Run adapter and frontier tests green.**

### Task 3: Rebuild the AI-first paper selection workbench

**Files:**
- Modify: `utils/journal_selection_service.py`, `utils/ai_service.py`, `ui/journal_selection_dialog.py`, `ui/paper_page.py`, `ui/journal_library_page.py`
- Test: `tests/test_journal_selection_v11.py`, `tests/test_journal_selection_dialog_v11.py`, `tests/test_ai_contracts_v11.py`, `tests/test_selection_v111.py`

**Interfaces:**
- Produces: normalized candidate with `source`, `ai_fit_score`, `verified_score`, `time_score`, `total_score`, `is_external`, `journal`.
- Consumes: `selection_requirements` with OA, quartile and `speed_priority` controls.

- [ ] **Step 1: Write failing tests for abstract forwarding, AI-dominant 70/30 scores, external candidates, quick import and strict Q3/Q4 filtering only for verified data.**
- [ ] **Step 2: Run the tests to confirm current local-first ±10 behavior fails the new contract.**
- [ ] **Step 3: Add pure candidate normalization/scoring and safe external-import helper.**
- [ ] **Step 4: Extend the DeepSeek schema and dialog controls, with no key fallback.**
- [ ] **Step 5: Wire a selected external candidate through fast library import before adding to a submission path.**
- [ ] **Step 6: Run selection, dialog and AI contract tests green.**

### Task 4: Visual, performance and data-safety verification

**Files:**
- Modify: `docs/qa/v11.1-refinement-verification.md`
- Test: all `tests/`

- [ ] **Step 1: Run pytest with third-party plugin autoload disabled and record count.**
- [ ] **Step 2: Measure same-theme Settings save and changed-theme switch using a temporary data root; record before/after milliseconds.**
- [ ] **Step 3: Capture HOME, WORK, PAPERS, LIBRARY and Settings at widget and software widths; inspect clipping, hierarchy and dropdown contrast.**
- [ ] **Step 4: Run compileall and read-only self-check against the formal data path.**
- [ ] **Step 5: Build installer only after all checks pass, then run an isolated default-path upgrade preservation check.**
