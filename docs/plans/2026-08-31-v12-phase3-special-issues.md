# 科研助手 v12.0 阶段三：LIBRARY 特刊征稿 Implementation Plan

> **For agentic workers:** REQUIRED SKILLS: use `test-driven-development`, `systematic-debugging`, and `verification-before-completion`. Execute inline only after phases one and two pass; no subagent delegation or intermediate installer.

**Goal:** 在 LIBRARY 增加小组件优先的特刊征稿页和独立 1024x768 工作台，实现多源发现、官网核验、逐篇论文匹配、收藏/关联/投稿路径/任务和截止提醒。

**Architecture:** 发现与官网证据写入可重建 SQLite，个人收藏、忽略、关联和提醒写入 `special_issues.json`。来源适配器统一规范化 Special Issue、Topical Collection、Research Topic 和 Article Collection；AI 在本地门槛之后阅读完整范围并匹配综合画像与逐篇论文画像。跨论文/期刊/任务动作使用原子多文件事务。

**Tech Stack:** Python 3.12、PySide6、sqlite3、现有网络帮助函数、EasyScholar、Qt tray notifications、Qt offscreen/QTest。

**Spec:** `docs/specs/2026-08-31-v12-research-intelligence-design.md`

## Global Constraints

- LIBRARY 页签顺序固定为“每日前沿、期刊库、特刊征稿”，默认每日前沿。
- 点击特刊页签只进入小组件；点击条目或工作台入口才打开独立 1024x768 窗口。
- 只收录期刊专题征稿，排除会议、Workshop、图书章节、普通常年征稿和仅邀请投稿。
- 正式结果必须有明确未来截止日期并达到最低匹配线。
- 聚合信息可在官网未核验时显示，但必须标为“未官网核验”并每天重试。
- 字段未知的结果在筛选后保留于列表末尾。
- 所有动作成功后窗口保持打开。
- 本阶段不得构建或安装中间版本。
- Python 命令统一使用 `S:\Python\Scripts\python.exe`。
- 当前目录不是 Git 仓库；以自动化测试和 QA 报告作为检查点。

## File Map

- Modify `utils/file_manager.py`: 注册 `special_issues.json`、备份和规范化。
- Create `utils/special_issue_repository.py`: 个人状态原子存储。
- Create `utils/special_issue_service.py`: 数据模型、类型过滤、去重、筛选和状态变化。
- Create `utils/special_issue_sources.py`: 聚合与出版社来源适配器。
- Modify `utils/evidence_cache.py`: 特刊发现、官网核验和检查点操作。
- Create `utils/special_issue_matching.py`: 综合/逐篇画像和 AI 匹配。
- Modify `utils/ai_service.py`: 完整征稿范围匹配契约。
- Create `utils/action_transaction.py`: 多文件原子动作。
- Modify `utils/journal_selection_service.py`: 将已核验库外期刊转换为期刊库记录。
- Modify `utils/submission_reminders.py`: 特刊提醒时间点与状态变化。
- Create `ui/special_issue_page.py`: LIBRARY 小组件页。
- Create `ui/special_issue_dialog.py`: 1024x768 工作台。
- Modify `ui/workbench_shell.py`: LIBRARY 第三页签和路由。
- Modify `ui/main_window.py`: 页面注册、启动刷新和桌面通知。
- Modify `ui/theme.py`: 特刊小组件、列表、详情和状态样式。
- Create `tests/test_v12_special_issue_repository.py`.
- Create `tests/test_v12_special_issue_sources.py`.
- Create `tests/test_v12_special_issue_service.py`.
- Create `tests/test_v12_special_issue_matching.py`.
- Create `tests/test_v12_special_issue_actions.py`.
- Create `tests/test_v12_special_issue_reminders.py`.
- Create `tests/test_v12_special_issue_widget_ui.py`.
- Create `tests/test_v12_special_issue_dialog_ui.py`.
- Extend `scripts/render_v12_ui.py`.
- Create `docs/qa/v12-phase3-acceptance.md`.

---

### Task 1: Add The Personal Special-Issue Store

**Files:**
- Modify: `utils/file_manager.py`
- Create: `utils/special_issue_repository.py`
- Create: `tests/test_v12_special_issue_repository.py`

**Interfaces:**
- Produces: `normalize_special_issue_store(raw: Any) -> dict[str, Any]`.
- Produces: `load_special_issue_store() -> dict[str, Any]`.
- Produces: `save_special_issue_store(store: dict[str, Any]) -> None`.
- Store keys: `version`, `items`, `last_checked_at`, `last_refresh_status`, `notification_log`, `reminder_log`.
- Personal item keys: `id`, `status`, `linked_paper_ids`, `created_task_ids`, `submission_path_refs`, `first_seen_at`, `last_seen_at`, `last_read_at`, `last_notified_at`, `deadline_history`, `verification_history`.

- [ ] **Step 1: Write failing normalization and atomic-write tests**

```python
def test_duplicate_personal_rows_merge_without_losing_links():
    store = normalize_special_issue_store({"items": [
        {"id": "si-1", "linked_paper_ids": ["p1"]},
        {"id": "si-1", "linked_paper_ids": ["p2"], "status": "saved"},
    ]})
    assert store["items"][0]["linked_paper_ids"] == ["p1", "p2"]
    assert store["items"][0]["status"] == "saved"
```

- [ ] **Step 2: Run repository tests and confirm missing store APIs**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_repository.py -q
```

- [ ] **Step 3: Register `SPECIAL_ISSUES_FILE` across data-root changes and backups**

Add the filename to `_set_data_dir`, backup allowlists, legacy import allowlists and restore validation. The default is an empty versioned object, never demo personal data.

- [ ] **Step 4: Implement deterministic normalization and atomic persistence**

Deduplicate by stable ID, union links/tasks/path refs in insertion order, preserve strongest personal status (`saved` over `read` over `unread`; `ignored` remains explicit), bound histories and write with temporary-file validation plus replace.

- [ ] **Step 5: Run repository and backup tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_repository.py tests/test_data_root_and_legacy_safety.py -q
```

- [ ] **Step 6: Record the checkpoint**

Record empty-store and duplicate-merge hashes using the temporary test root.

---

### Task 2: Build Source Adapters, Type Admission And Deduplication

**Files:**
- Create: `utils/special_issue_sources.py`
- Create: `utils/special_issue_service.py`
- Modify: `utils/evidence_cache.py`
- Create: `tests/test_v12_special_issue_sources.py`
- Create: `tests/test_v12_special_issue_service.py`

**Interfaces:**
- Produces protocol: `SpecialIssueSource.fetch(*, since: datetime, progress: Callable[..., None] | None = None) -> list[dict[str, Any]]`.
- Produces adapters: `FrontiersResearchTopicsSource`, `ElsevierCallsSource`, `SpringerCollectionsSource`, `WileyCallsSource`, `MdpiSpecialIssuesSource`, and `AggregatorDiscoverySource`.
- Produces: `normalize_special_issue(raw: Any, *, source: str, fetched_at: str) -> dict[str, Any] | None`.
- Produces: `special_issue_dedupe_key(item: dict[str, Any]) -> str`.
- Produces: `merge_special_issue_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write failing type, deadline, normalization and dedupe tests**

Allow normalized types `special_issue`, `topical_collection`, `research_topic`, `article_collection`. Reject conference/workshop/book chapter/rolling call/invitation-only. Reject missing or past deadlines. Assert same official URL merges; otherwise same ISSN + normalized title + deadline merges and retains all source evidence.

- [ ] **Step 2: Run source/service tests and confirm modules are absent**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_sources.py tests/test_v12_special_issue_service.py -q
```

- [ ] **Step 3: Implement the shared normalized record**

Required keys: `id`, `dedupe_key`, `title`, `type`, `journal`, `issns`, `publisher`, `scope_text`, `deadline`, `official_url`, `discovery_urls`, `source_evidence`, `fee_mode`, `jcr`, `cas`, `verification_status`, `fetched_at`, `official_checked_at`, `status`.

- [ ] **Step 4: Implement adapters with structured-data-first parsing**

Each adapter tries JSON/JSON-LD/RSS/public list before page parsing. Use source-specific fixtures and existing request helpers. A single adapter failure returns a source error event and does not abort the refresh.

- [ ] **Step 5: Add cache TTL and checkpoint behavior**

Cache successful discovery for 24 hours. Use exponential retry timestamps after source failure. Store page/record payload hashes so unchanged responses do not generate new user items.

- [ ] **Step 6: Implement type admission and dedupe**

Normalize case/Unicode/HTML, parse ISO dates, require future deadline, apply exclusion phrases, then merge source evidence. Do not discard unknown publisher/division/fee fields at this stage.

- [ ] **Step 7: Run source/service/cache tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_sources.py tests/test_v12_special_issue_service.py tests/test_v12_evidence_cache.py -q
```

- [ ] **Step 8: Record the checkpoint**

Record per-source fixture counts, rejected type counts and deduplicated output count.

---

### Task 3: Verify Official Status And Enrich Journal Metadata

**Files:**
- Modify: `utils/special_issue_service.py`
- Modify: `utils/journal_service.py`
- Modify: `utils/easyscholar_service.py`
- Modify: `utils/evidence_cache.py`
- Create: `tests/test_v12_special_issue_service.py`

**Interfaces:**
- Produces: `verify_special_issue(item: dict[str, Any], *, now: datetime, cache: EvidenceCache) -> dict[str, Any]`.
- Produces: `enrich_special_issue_journal(item: dict[str, Any], journal_library: list[dict[str, Any]], *, easyscholar_ready: bool) -> dict[str, Any]`.
- Verification statuses: `official_verified`, `aggregator_unverified`, `closed`, `expired`, `conflict`, `temporarily_unavailable`.

- [ ] **Step 1: Write failing official, aggregator and status-change tests**

Assert an official page with matching journal/title/future deadline is verified; a fresh aggregator row with deadline is retained as unverified; official closed/expired state updates status; deadline changes append history; temporary official failure does not close a call.

- [ ] **Step 2: Run service tests and confirm missing verifier**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_service.py -q
```

- [ ] **Step 3: Implement conservative official verification**

Match normalized title fragments, journal/ISSN and deadline from official content. Do not consider a successful HTTP status alone sufficient. Store the official page hash and checked time.

- [ ] **Step 4: Apply aggregator fallback exactly**

If the aggregator record is within its source freshness window and has a future deadline, set `aggregator_unverified`. Show it in formal results with the warning label and retry official verification daily.

- [ ] **Step 5: Enrich from journal library and EasyScholar**

Match by ISSN first and fuzzy name second. Add publisher, OA/fee, JCR and CAS evidence with source/timestamp. Unknown fields stay `unknown`; do not invent values.

- [ ] **Step 6: Run verifier, journal and EasyScholar tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_service.py tests/test_journal_library_easyscholar_v111.py tests/test_easyscholar_service_v111.py -q
```

- [ ] **Step 7: Record the checkpoint**

Record official verified, aggregator unverified, closed and changed-deadline fixture counts.

---

### Task 4: Match Global And Per-Paper Profiles With Full-Scope AI Reading

**Files:**
- Create: `utils/special_issue_matching.py`
- Modify: `utils/ai_service.py`
- Create: `tests/test_v12_special_issue_matching.py`

**Interfaces:**
- Produces: `build_special_issue_profiles(research_profile: dict[str, Any], papers: list[dict[str, Any]]) -> dict[str, Any]` with `global` and `papers`.
- Produces: `match_special_issue(item: dict[str, Any], profiles: dict[str, Any], *, progress: Callable[..., None] | None = None) -> dict[str, Any]`.
- Match result keys: `score`, `reason`, `matched_terms`, `matched_papers`, `profile_scope`, `model`, `evaluated_at`.
- Defaults: formal minimum score `60`; high-notification threshold `80`.

- [ ] **Step 1: Write failing global/per-paper, exclusion and single-card tests**

Assert all submission papers contribute to global profile, each paper retains its own title/keywords/abstract profile, excluded terms reduce or reject matches, and one special issue matched to p1/p2 returns one result with two `matched_papers` rows.

- [ ] **Step 2: Run matching tests and confirm missing module**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_matching.py -q
```

- [ ] **Step 3: Build profile payloads without losing locks or blocks**

Global payload contains active/locked/excluded terms and authored/submission paper summaries. Per-paper payload contains only that paper plus shared locked/excluded context. Never send API keys, file paths or full OCR cache.

- [ ] **Step 4: Require full scope in the AI contract**

The prompt includes complete available `scope_text`, call type, journal and deadline, and requests one total score, concise reason, matched terms and per-paper scores. If full scope is absent, mark `awaiting_scope` instead of scoring.

- [ ] **Step 5: Apply local post-validation**

Clamp score 0..100, require reason, remove unknown paper IDs, apply excluded/blocked term conflicts and keep only results scoring at least 60 in formal recommendation lists.

- [ ] **Step 6: Run matching and profile tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_matching.py tests/test_v12_research_profile.py tests/test_v12_research_signals.py -q
```

- [ ] **Step 7: Record the checkpoint**

Record deterministic fixtures for global-only, one-paper, multi-paper, excluded and awaiting-scope outcomes.

---

### Task 5: Implement Transactional Actions And Deadline Reminders

**Files:**
- Create: `utils/action_transaction.py`
- Modify: `utils/special_issue_repository.py`
- Modify: `utils/journal_selection_service.py`
- Modify: `utils/file_manager.py`
- Modify: `utils/submission_reminders.py`
- Create: `tests/test_v12_special_issue_actions.py`
- Create: `tests/test_v12_special_issue_reminders.py`

**Interfaces:**
- Produces: `apply_json_transaction(changes: dict[Path, Any]) -> None`.
- Produces: `associate_special_issue(issue_id: str, paper_ids: list[str]) -> None`.
- Produces: `add_special_issue_to_submission_path(issue_id: str, paper_id: str) -> dict[str, str]`.
- Produces: `create_special_issue_preparation_task(issue_id: str, paper_id: str) -> str`.
- Produces: `special_issue_reminder_offsets() -> tuple[int, ...]`, returning `(90, 60, 30, 14, 7, 3)`.
- Produces: `collect_special_issue_reminders(store: dict[str, Any], *, today: date) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write failing association, path, task and rollback tests**

Assert association changes only `special_issues.json`; path action quick-imports a missing journal then appends a candidate with special title/deadline; task action creates one linked todo; injected failure on the second file restores every original byte.

- [ ] **Step 2: Run action/reminder tests and confirm APIs are missing**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_actions.py tests/test_v12_special_issue_reminders.py -q
```

- [ ] **Step 3: Implement multi-file atomic transaction**

Validate all normalized payloads first, write every temporary file, fsync, then replace targets. Before replacing, copy current files to transaction backups. If any replace fails, restore replaced files and delete only transaction temporaries/backups.

- [ ] **Step 4: Implement the three action boundaries exactly**

Association writes no journal/path/task. Path ensures the verified journal exists and creates a submission candidate containing issue ID/title/deadline. Task creates a due-date todo linked to issue and paper without changing the path.

- [ ] **Step 5: Implement reminders and status-change events**

Only saved calls receive offset reminders. Deduplicate each issue/date/offset notification. Deadline change and closed events bypass offsets and create separate notifications.

- [ ] **Step 6: Run action, reminder, paper and backup regressions**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_actions.py tests/test_v12_special_issue_reminders.py tests/test_v1156_journal_actions.py tests/test_system_integration_v11.py tests/test_data_root_and_legacy_safety.py -q
```

- [ ] **Step 7: Record the checkpoint**

Include transaction rollback hashes and reminder dates for a fixed deadline fixture.

---

### Task 6: Add The LIBRARY Special-Issue Widget

**Files:**
- Create: `ui/special_issue_page.py`
- Modify: `ui/workbench_shell.py`
- Modify: `ui/main_window.py`
- Modify: `ui/theme.py`
- Create: `tests/test_v12_special_issue_widget_ui.py`
- Modify: `tests/test_workbench_routing_v11.py`
- Extend: `scripts/render_v12_ui.py`

**Interfaces:**
- Adds route: `special_issues -> RouteTarget("library", "special_issues")`.
- Adds page key: `pages["special_issues"]`.
- `SpecialIssuePage.open_workbench = Signal(str)` where the string is optional selected issue ID.
- UI object names: `specialIssueSummary`, `specialIssueUnreadCount`, `specialIssueSavedCount`, `specialIssueNearestDeadline`, `specialIssueSavedList`, `specialIssueRecommendedList`, `specialIssueOpenWorkbench`.

- [ ] **Step 1: Write failing route, default-tab and 400x480 tests**

Assert LIBRARY contains three chips in order, initial LIBRARY anchor is frontier, clicking special issues does not open the dialog automatically, saved rows appear before matching rows, summary shows unread/nearest deadline, and all rows/buttons fit 400x480.

- [ ] **Step 2: Run widget UI tests and confirm the third page is missing**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_widget_ui.py -q
```

- [ ] **Step 3: Add the third LIBRARY route without changing the default**

Extend route aliases and `_SECTIONS["library"]` to frontier, journals, special_issues. Keep `library -> frontier` and initial main route HOME unchanged.

- [ ] **Step 4: Build the compact special-issue surface**

Use one summary strip and two divided lists: saved first, then highest-match formal results. At 400x480 cap visible preview rows and rely on the scrolling list/open-workbench action. Do not nest cards.

- [ ] **Step 5: Bind row and workbench actions**

Clicking a row emits its issue ID and opens the large workbench at that item. The explicit workbench icon emits an empty ID and opens the default recommendation list.

- [ ] **Step 6: Run route, widget and theme tests**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_widget_ui.py tests/test_workbench_routing_v11.py tests/test_theme_settings_v11.py tests/test_v115_cross_page_ui.py -q
```

- [ ] **Step 7: Render widget screenshots**

Render no-data, saved-first, high-match, unverified and imminent-deadline states at 400x480 and 480x720 for every theme.

- [ ] **Step 8: Record the checkpoint**

Record route assertions, preview ordering and geometry scan results.

---

### Task 7: Build The 1024x768 Special-Issue Workbench

**Files:**
- Create: `ui/special_issue_dialog.py`
- Modify: `ui/special_issue_page.py`
- Modify: `ui/main_window.py`
- Modify: `ui/theme.py`
- Create: `tests/test_v12_special_issue_dialog_ui.py`
- Extend: `scripts/render_v12_ui.py`

**Interfaces:**
- Produces: `SpecialIssueDialog(store: dict[str, Any], items: list[dict[str, Any]], papers: list[dict[str, Any]], selected_issue_id: str = "", parent: QWidget | None = None)`.
- Signals: `changed`, `associate_requested(str, list[str])`, `path_requested(str, str)`, `task_requested(str, str)`, `refresh_requested`.
- UI object names: `specialProfileScope`, `specialPublisherFilter`, `specialFeeFilter`, `specialJcrFilter`, `specialCasFilter`, `specialDeadlineFilter`, `specialRefreshButton`, `specialRefreshProgress`, `specialResultList`, `specialDetailPane`, `specialVerificationEvidence`, `specialAssociateButton`, `specialPathButton`, `specialTaskButton`.

- [ ] **Step 1: Write failing 1024x768, filter, unknown-last and persistence tests**

Assert left/right panes fit, profile selector offers global plus papers, filter-known matches appear first, unknown fields remain last, selected card details match, actions remain visible, and successful actions do not close the dialog or lose selection.

- [ ] **Step 2: Run dialog UI tests and confirm the workbench is absent**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_dialog_ui.py -q
```

- [ ] **Step 3: Build the two-pane workbench**

Top toolbar holds profile scope, filters, search, refresh and progress. Left pane uses tabs for recommended/saved/unverified/status changes. Right pane shows scope, score, reason, matched papers, metadata, deadline and expandable evidence.

- [ ] **Step 4: Implement filter semantics**

Known mismatches are filtered. Unknown publisher/fee/division remains in a separate tail group with an `信息待补` label. Deadline filter always requires a known future deadline because deadline is a formal admission requirement.

- [ ] **Step 5: Wire actions transactionally and preserve state**

Disable only the pressed action during work, show progress, call Task 5 services, reload store/papers/journals, keep the dialog open, restore selected ID and scroll position, and show inline success/error state.

- [ ] **Step 6: Add evidence expansion**

Show discovery URLs, official URL, source timestamps, official check time, ISSN, EasyScholar results and deadline history. All URLs are clickable; no evidence appears as decorative text.

- [ ] **Step 7: Run dialog, action and theme tests**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_dialog_ui.py tests/test_v12_special_issue_actions.py tests/test_theme_settings_v11.py -q
```

- [ ] **Step 8: Render workbench screenshots**

Render recommendation, saved, unverified, status-change, expanded-evidence, loading, empty and error states at 1024x768 for all themes. Run geometry and pixel-variance checks.

- [ ] **Step 9: Record the checkpoint**

Include current-selection preservation and action-window-visible assertions.

---

### Task 8: Add 24-Hour Refresh And Notification Policy

**Files:**
- Modify: `ui/main_window.py`
- Modify: `ui/special_issue_page.py`
- Modify: `utils/special_issue_service.py`
- Modify: `utils/special_issue_repository.py`
- Modify: `utils/submission_reminders.py`
- Create: `tests/test_v12_special_issue_reminders.py`
- Create: `tests/test_v12_special_issue_service.py`

**Interfaces:**
- Produces: `special_issue_refresh_due(last_checked_at: str, *, now: datetime) -> bool`.
- Produces: `build_special_issue_notifications(before: dict[str, Any], after: dict[str, Any], *, today: date) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write failing 24-hour and notification-policy tests**

Assert 23h59m is not due, 24h is due, new score 79 gives no desktop notification, new score 80 does, saved offset dates notify once, and deadline/closed changes notify regardless of score.

- [ ] **Step 2: Run refresh/reminder tests and observe missing policy helpers**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_service.py tests/test_v12_special_issue_reminders.py -q
```

- [ ] **Step 3: Schedule refresh after first paint**

MainWindow starts a background refresh only when due. Manual refresh bypasses due checking but honors the running-task lock. Startup remains responsive and shows status in the special-issue widget when the user visits it.

- [ ] **Step 4: Apply notification rules**

Only new formal results at score >=80 notify. Saved reminders use fixed offsets. Deadline changes and closure have separate titles. Deduplicate through `notification_log`.

- [ ] **Step 5: Run reminder, main-window and integration tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_service.py tests/test_v12_special_issue_reminders.py tests/test_system_integration_v11.py -q
```

- [ ] **Step 6: Record the checkpoint**

Record due/not-due boundary and deduped notification counts.

---

### Task 9: Phase-Three Data And UI Gate

**Files:**
- Create/Finalize: `docs/qa/v12-phase3-acceptance.md`
- No packaging changes.

**Interfaces:**
- Produces: the final feature-phase acceptance report required by the release plan.

- [ ] **Step 1: Run all special-issue service tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_repository.py tests/test_v12_special_issue_sources.py tests/test_v12_special_issue_service.py tests/test_v12_special_issue_matching.py tests/test_v12_special_issue_actions.py tests/test_v12_special_issue_reminders.py -q
```

- [ ] **Step 2: Run special-issue UI tests offscreen**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_special_issue_widget_ui.py tests/test_v12_special_issue_dialog_ui.py tests/test_workbench_routing_v11.py tests/test_theme_settings_v11.py -q
```

- [ ] **Step 3: Run the complete v12 phase-one and phase-two suites**

Use the exact commands recorded in the two accepted phase reports. Reject any regression.

- [ ] **Step 4: Run source failure and stale-data matrix**

Test each source with fresh data, stale data, 403, timeout, malformed JSON/HTML and official-page mismatch. Confirm aggregator fallback, warnings and daily retry behavior.

- [ ] **Step 5: Run cross-file transaction failure matrix**

Inject failures before temporary write, after temporary write, during first replace and during later replace. Assert original hashes are restored every time.

- [ ] **Step 6: Review all widget and workbench screenshots**

Reject clipped deadline text, missing unread count, hidden actions, nested cards, unknown results mixed above known matches, blank progress or excessive motion.

- [ ] **Step 7: Finalize the phase report**

Record exact pass counts, source matrix, transaction hashes, notification matrix, screenshot inventory and residual source-maintenance risks. State that no installer was built and no formal data was modified.

- [ ] **Step 8: Advance to release only after all three phase reports pass**

Begin `docs/plans/2026-08-31-v12-release-install.md` only when phases one, two and three are all accepted.
