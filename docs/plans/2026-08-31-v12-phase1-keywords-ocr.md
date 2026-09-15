# 科研助手 v12.0 阶段一：关键词与 OCR Implementation Plan

> **For agentic workers:** REQUIRED SKILLS: use `test-driven-development` for every behavior change, `systematic-debugging` for failures, and `verification-before-completion` at the phase gate. Execute inline in the current task; the user did not authorize subagent delegation.

**Goal:** 只保留小组件模式，建立可回滚的 v12 研究画像、本机扫描 PDF OCR、每日 AI 整理和 1024x768 关键词工作台。

**Architecture:** 个人画像保存在独立 `research_profile.json`，旧关键词仅迁移一次，手动删除通过永久阻止记录防止复活。PDF 先读文字层，再对缺字页面使用 PDFium 与 RapidOCR，本机缓存 OCR，AI 只负责清理和结构化提取。所有写入使用原子替换，AI 失败不改变正式画像。

**Tech Stack:** Python 3.12、PySide6、pypdf、pypdfium2、RapidOCR、ONNX Runtime、SQLite/JSON、unittest/pytest、Qt offscreen/QTest。

**Spec:** `docs/specs/2026-08-31-v12-research-intelligence-design.md`

## Global Constraints

- v12.0 只提供小组件模式，最小尺寸固定为 400x480。
- 研究画像工作台固定以 1024x768 为设计基准，并允许随可用屏幕缩小。
- 本阶段不得构建或安装中间版本。
- 不读写正式安装目录的数据；测试统一使用 `RESEARCH_ASSISTANT_DATA_DIR` 临时目录。
- 所有手动代码编辑使用 `apply_patch`。
- Python 命令统一使用 `S:\Python\Scripts\python.exe`。
- 当前目录不是 Git 仓库，因此任务以测试结果和 QA 报告作为检查点，不执行伪造的提交步骤。
- 运动实现只用 Qt 原生动画；Taste/GSAP/Ponytail 作为设计与实现约束，不引入网页运行时。

## File Map

- Modify `utils/window_mode.py`: 将公开模式策略收敛为 widget。
- Modify `utils/file_manager.py`: 迁移设置、注册 v12 画像文件路径和兼容读取。
- Modify `main.py`: 在导入持久化与 UI 前完成数据目录 bootstrap。
- Create `utils/storage_bootstrap.py`: 选择稳定 UserData、复制校验旧数据、设置进程数据根。
- Modify `utils/research_profile_service.py`: v12 词条、翻译、锁定、排除和永久阻止规则。
- Create `utils/research_profile_repository.py`: 原子画像存储、单快照和当天撤销。
- Modify `utils/pdf_text_service.py`: 文字层、PDFium 渲染和 OCR 混合提取。
- Modify `utils/ai_service.py`: OCR 清理、结构化关键词和画像整理契约。
- Modify `ui/settings_dialog.py`: 移除软件模式入口。
- Modify `ui/workbench_shell.py`: 删除连续软件视图，只保留 widget 栈。
- Modify `ui/main_window.py`: 固定小组件行为、启动每日整理。
- Create `ui/motion.py`: 少量共享 Qt 运动策略和减少动态效果检测。
- Modify `ui/research_profile_dialog.py`: 三区域关键词工作台。
- Modify `ui/frontier_settings_dialog.py`: OCR/AI 分段进度和新画像读写。
- Modify `ui/theme.py`: v12 工具排版、状态和关键词行样式。
- Add selected official Lucide SVG files under `assets/icons/lucide/` and `assets/icons/lucide/LICENSE`.
- Create `ui/icons.py`: 从已打包 Lucide SVG 加载 `QIcon`。
- Create `tests/test_v12_widget_only.py`.
- Create `tests/test_v12_storage_bootstrap.py`.
- Create `tests/test_v12_research_profile.py`.
- Create `tests/test_v12_profile_repository.py`.
- Create `tests/test_v12_pdf_ocr.py`.
- Create `tests/test_v12_profile_ai_contracts.py`.
- Create `tests/test_v12_phase1_ui.py`.
- Create `scripts/render_v12_ui.py`.
- Create `docs/qa/v12-phase1-acceptance.md` at the phase gate.

---

### Task 1: Collapse The Application To Widget Mode

**Files:**
- Modify: `utils/window_mode.py`
- Modify: `utils/file_manager.py`
- Modify: `ui/settings_dialog.py`
- Modify: `ui/workbench_shell.py`
- Modify: `ui/main_window.py`
- Create: `tests/test_v12_widget_only.py`
- Modify: `tests/test_workbench_routing_v11.py`
- Modify: `tests/test_window_mode_v11.py`

**Interfaces:**
- Produces: `normalize_application_mode(value: object) -> str`, always returning `"widget"`.
- Produces: `mode_window_key(mode: object) -> str`, always returning `"widget_window"`.
- Produces: `mode_minimum_size(mode: object) -> tuple[int, int]`, always returning `(400, 480)`.
- Produces: `WorkbenchShell.set_mode(mode: str = "widget") -> None`, retaining compatibility while building only the widget stack.

- [ ] **Step 1: Write failing widget-only policy tests**

```python
def test_legacy_software_setting_migrates_to_widget():
    assert normalize_application_mode("software") == "widget"
    assert mode_window_key("software") == "widget_window"
    assert mode_minimum_size("software") == (400, 480)

def test_settings_dialog_has_no_application_mode_selector(qapp):
    dialog = SettingsDialog({"application_mode": "software"})
    assert not hasattr(dialog, "application_mode_selector")
    assert dialog.settings()["application_mode"] == "widget"
```

- [ ] **Step 2: Run the focused tests and confirm the old behavior fails**

Run:

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_widget_only.py -q
```

Expected: failure because `software` still normalizes to software and the selector still exists.

- [ ] **Step 3: Remove the software-mode branch while preserving route compatibility**

Implement the three policy functions exactly as specified, remove the “打开方式” selector and its save plumbing, normalize saved settings to `application_mode="widget"`, and reduce `WorkbenchShell` to `_build_widget_shell()` plus existing route resolution. Keep legacy route aliases unchanged.

- [ ] **Step 4: Update old mode tests to assert migration instead of dual-mode rendering**

Delete assertions that require continuous software scrolling. Retain tests for HOME/WORK/PAPERS/LIBRARY route stability and widget page ownership.

- [ ] **Step 5: Run widget, routing, settings, HOME and WORK regression tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_widget_only.py tests/test_workbench_routing_v11.py tests/test_window_mode_v11.py tests/test_home_layout_v11.py tests/test_todo_layout_v11.py tests/test_theme_settings_v11.py -q
```

Expected: all pass; no software-mode UI remains.

- [ ] **Step 6: Record the task checkpoint**

Append the command, pass count and timestamp to `docs/qa/v12-phase1-acceptance.md` under “Widget-only foundation”.

---

### Task 2: Bootstrap A Stable UserData Root Without Touching Formal Data

**Files:**
- Create: `utils/storage_bootstrap.py`
- Modify: `main.py`
- Modify: `utils/file_manager.py`
- Create: `tests/test_v12_storage_bootstrap.py`
- Modify: `tests/test_data_root_and_legacy_safety.py`

**Interfaces:**
- Produces: `default_user_data_dir(local_app_data: Path) -> Path`.
- Produces: `build_data_manifest(directory: Path) -> dict[str, Any]`.
- Produces: `copy_data_store_verified(source: Path, destination: Path) -> dict[str, Any]`.
- Produces: `prepare_v12_data_root(*, frozen: bool | None = None, app_root: Path | None = None, local_app_data: Path | None = None) -> Path`.

- [ ] **Step 1: Write failing selection, copy and rollback tests**

```python
def test_default_root_is_outside_programs(tmp_path):
    assert default_user_data_dir(tmp_path) == tmp_path / "科研助手" / "UserData"

def test_verified_copy_preserves_hashes_and_leaves_source(tmp_path):
    source = tmp_path / "Programs" / "科研助手" / "data"
    destination = tmp_path / "科研助手" / "UserData"
    source.mkdir(parents=True)
    (source / "papers.json").write_text('[{"id":"p1"}]', encoding="utf-8")
    manifest = copy_data_store_verified(source, destination)
    assert (source / "papers.json").exists()
    assert manifest["files"]["papers.json"]["source_sha256"] == manifest["files"]["papers.json"]["destination_sha256"]
```

- [ ] **Step 2: Run focused storage tests and confirm imports/functions are missing**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_storage_bootstrap.py -q
```

- [ ] **Step 3: Implement pure manifest and copy helpers**

Use `hashlib.sha256`, `json`, `shutil.copy2`, temporary destination directories and `Path.replace`. Do not merge divergent files. On any parse/hash failure, delete only the temporary destination and leave source/config unchanged.

- [ ] **Step 4: Bootstrap before importing `ui.main_window` or `utils.file_manager`**

At the top of `main.py`, call `prepare_v12_data_root()` before importing modules that bind `DATA_DIR`. The function must honor `RESEARCH_ASSISTANT_DATA_DIR` first so tests remain isolated.

- [ ] **Step 5: Register `RESEARCH_PROFILE_FILE` without migrating live data yet**

Add `RESEARCH_PROFILE_FILE = DATA_DIR / "research_profile.json"` and update `_set_data_dir()`, backup file names, directory import and restore allowlists.

- [ ] **Step 6: Run storage safety regressions**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_storage_bootstrap.py tests/test_data_root_and_legacy_safety.py -q
```

Expected: copies are byte-identical, source data survives, environment override remains highest priority.

- [ ] **Step 7: Record the task checkpoint**

Document only temporary test paths and hashes. Never write the real API key or formal data path into the report body.

---

### Task 3: Implement The V12 Research Profile Model And Permanent Blocks

**Files:**
- Modify: `utils/research_profile_service.py`
- Create: `tests/test_v12_research_profile.py`
- Modify: `tests/test_research_profile_v11.py`

**Interfaces:**
- Produces: `normalize_research_profile_v12(raw: Any, *, today: str | None = None) -> dict[str, Any]`.
- Keeps: `normalize_research_profile_v11(raw: Any) -> dict[str, Any]` as a compatibility wrapper.
- Produces: `remove_term(profile: Any, term_id: str, *, permanent: bool = True, today: str | None = None) -> dict[str, Any]`.
- Produces: `reject_pending_term(profile: Any, term_id: str, *, today: str | None = None) -> dict[str, Any]`, writing a permanent block.
- Produces: `update_term_fields(profile: Any, term_id: str, *, canonical_en: str | None = None, translation_zh: str | None = None, weight: int | None = None) -> dict[str, Any]`.
- Produces: `upsert_excluded_term(profile: Any, *, canonical_en: str, translation_zh: str, locked: bool = False, source: str = "user") -> dict[str, Any]`.
- Produces: `delete_excluded_term(profile: Any, term_id: str) -> dict[str, Any]`.

- [ ] **Step 1: Write failing migration, persistence, translation and lock tests**

```python
def test_removed_legacy_term_never_resurrects_after_twenty_normalizations():
    profile = normalize_research_profile_v12({"primary_keywords": ["soil carbon"]})
    term_id = profile["terms"][0]["id"]
    profile = remove_term(profile, term_id, today="2026-08-31")
    for _ in range(20):
        profile = normalize_research_profile_v12(profile)
    assert profile["terms"] == []
    assert profile["blocked_terms"][0]["canonical_key"] == "soil carbon"

def test_locked_term_rejects_ai_changes_but_translation_is_user_editable():
    profile = normalize_research_profile_v12({"terms": [{"canonical_en": "soil carbon", "translation_zh": "土壤碳", "locked": True}]})
    updated = update_term_fields(profile, profile["terms"][0]["id"], translation_zh="土壤有机碳")
    assert updated["terms"][0]["translation_zh"] == "土壤有机碳"
    assert updated["terms"][0]["weight"] == 100
```

- [ ] **Step 2: Run the profile tests and confirm v11 normalization fails the new contract**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_research_profile.py -q
```

- [ ] **Step 3: Implement canonical v12 term normalization**

Normalize each active/pending/excluded entry to `id`, `canonical_en`, `translation_zh`, `aliases`, `status`, `weight`, `locked`, `confidence`, `sources`, `evidence`, `created_at`, `updated_at`, and `last_signal_at`. Sort active terms by `(-weight, canonical_en.casefold())`.

- [ ] **Step 4: Make legacy migration idempotent**

Migrate `primary_keywords`, `secondary_keywords` and `negative_keywords` only when `legacy_profile_migrated_at` is absent. Preserve old fields for rollback, set the marker, and filter every migrated alias against `blocked_terms`.

- [ ] **Step 5: Implement lock and permanent-block boundaries**

Locked entries reject AI rename, state, weight and deletion changes. User translation edits are allowed. Manual active deletion and pending rejection append a deduplicated block containing canonical key, alias keys, display text, reason and timestamp.

- [ ] **Step 6: Run new and old profile tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_research_profile.py tests/test_research_profile_v11.py -q
```

- [ ] **Step 7: Record the task checkpoint**

Include the 20-round reload result and blocked-term count in the phase report.

---

### Task 4: Add Atomic Profile Storage, Daily Organization And One-Step Undo

**Files:**
- Create: `utils/research_profile_repository.py`
- Modify: `utils/file_manager.py`
- Modify: `utils/ai_service.py`
- Modify: `ui/frontier_page.py`
- Modify: `ui/main_window.py`
- Create: `tests/test_v12_profile_repository.py`
- Create: `tests/test_v12_profile_ai_contracts.py`

**Interfaces:**
- Produces: `load_research_profile() -> dict[str, Any]`.
- Produces: `save_research_profile(profile: dict[str, Any]) -> None`.
- Produces: `apply_organization_transaction(current: dict[str, Any], proposal: dict[str, Any], *, today: str) -> tuple[dict[str, Any], list[dict[str, Any]]]`.
- Produces: `apply_and_save_ai_organization(organizer: Callable[[], dict[str, Any]], *, today: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]`, saving only after a valid proposal is applied.
- Produces: `undo_last_organization(profile: dict[str, Any], *, today: str) -> dict[str, Any]`.
- Produces: `should_auto_organize(profile: dict[str, Any], *, today: str) -> bool`.
- Produces: `organize_research_profile_with_ai(profile: dict[str, Any], evidence: dict[str, Any], progress: Callable[..., None] | None = None) -> dict[str, Any]`.

- [ ] **Step 1: Write failing transaction and undo tests**

```python
def test_ai_failure_keeps_profile_bytes_unchanged(tmp_path, monkeypatch):
    original = normalize_research_profile_v12({"terms": [{"canonical_en": "soil carbon", "translation_zh": "土壤碳"}]})
    save_research_profile(original)
    before = RESEARCH_PROFILE_FILE.read_bytes()
    with pytest.raises(RuntimeError):
        apply_and_save_ai_organization(lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    assert RESEARCH_PROFILE_FILE.read_bytes() == before

def test_only_latest_snapshot_can_be_undone_once():
    updated, _ = apply_organization_transaction(profile, proposal, today="2026-08-31")
    restored = undo_last_organization(updated, today="2026-08-31")
    assert restored["terms"] == profile["terms"]
    assert should_auto_organize(restored, today="2026-08-31") is False
```

- [ ] **Step 2: Run focused repository tests and confirm missing APIs**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_profile_repository.py tests/test_v12_profile_ai_contracts.py -q
```

- [ ] **Step 3: Implement atomic JSON persistence**

Write UTF-8 JSON to `research_profile.json.tmp`, flush and `os.fsync`, validate by re-reading, then replace the destination. Keep one `.previous` only during the transaction and remove it after a successful validated replace.

- [ ] **Step 4: Implement proposal validation and one snapshot**

Store `last_organization_snapshot` inside the profile only after AI returns valid structured changes. Keep exactly one snapshot. Apply changes through `research_profile_service` so locks and blocks remain authoritative.

- [ ] **Step 5: Implement once-per-day scheduling**

`MainWindow` schedules the organizer after first paint. `should_auto_organize` checks `last_auto_organization_date` and `auto_organization_suppressed_for_date`. Manual organization bypasses the daily check but still creates a fresh single snapshot.

- [ ] **Step 6: Run repository, startup and AI progress tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_profile_repository.py tests/test_v12_profile_ai_contracts.py tests/test_ai_progress_and_keywords_v1154.py tests/test_system_integration_v11.py -q
```

- [ ] **Step 7: Record the task checkpoint**

Report original hash, failed-run hash and undo semantic equality using only temporary fixtures.

---

### Task 5: Add Page-Level Local OCR With Text-Layer Fallback

**Files:**
- Modify: `requirements.txt`
- Modify: `utils/pdf_text_service.py`
- Create: `tests/test_v12_pdf_ocr.py`

**Interfaces:**
- Extends: `extract_pdf_full_text(path: str | Path, *, ocr_engine: Callable[[Any], Any] | None = None, progress: Callable[[str, int, int, str], None] | None = None, cancelled: Callable[[], bool] | None = None) -> dict[str, Any]`.
- Produces result keys: `name`, `path`, `fingerprint`, `page_count`, `text`, `pages`, `ocr_page_count`, `warnings`.
- Produces page keys: `page_number`, `text`, `source` (`native` or `ocr`), `confidence`, `fingerprint`.

- [ ] **Step 1: Add failing native, scanned, mixed and cancel tests**

Use a fake `ocr_engine(image) -> {"text": "soil organic carbon", "confidence": 0.93}` and injected page renderer for deterministic unit tests. Assert native pages do not call OCR, image-only pages do, mixed order is preserved, and cancellation raises `PdfTextExtractionError("已取消 OCR")` without profile writes.

- [ ] **Step 2: Run OCR tests and confirm scanned PDFs still raise the old error**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_pdf_ocr.py -q
```

- [ ] **Step 3: Add bounded dependencies**

Add compatible bounds for `pypdfium2`, `rapidocr`, `onnxruntime` and `Pillow`. Install them into the development environment with:

```powershell
& 'S:\Python\Scripts\python.exe' -m pip install -r requirements.txt
```

- [ ] **Step 4: Implement hybrid extraction with lazy imports**

Keep `pypdf` for native text. Render only pages below the effective text threshold. Create the RapidOCR engine once per worker, not once per page. Cache page results by PDF fingerprint plus page number and OCR model identifier.

- [ ] **Step 5: Add encrypted, corrupt and low-confidence handling**

Encrypted/corrupt files fail before OCR. Low-confidence pages remain in `warnings` and are never silently treated as clean text. A partly readable document returns successful pages plus warnings.

- [ ] **Step 6: Run OCR and existing PDF keyword tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_pdf_ocr.py tests/test_ai_progress_and_keywords_v1154.py -q
```

- [ ] **Step 7: Run one real local OCR smoke fixture**

Generate a two-page test PDF in the temporary test directory, with one native-text page and one rasterized Chinese/English page. Assert both marker phrases appear in page order. Do not use a personal PDF for this smoke test.

- [ ] **Step 8: Record the task checkpoint**

Record page counts, OCR page count, runtime and warning count for the generated fixture.

---

### Task 6: Make AI Keyword Extraction Structured, Translated And Safe

**Files:**
- Modify: `utils/ai_service.py`
- Modify: `utils/research_profile_service.py`
- Create: `tests/test_v12_profile_ai_contracts.py`
- Modify: `tests/test_ai_contracts_v11.py`

**Interfaces:**
- Produces: `clean_ocr_text_with_ai(pages: list[dict[str, Any]], progress: Callable[..., None] | None = None) -> dict[str, Any]`.
- Extends: `extract_research_keywords_with_ai(source: dict[str, Any], progress: Callable[..., None] | None = None) -> dict[str, Any]`.
- AI keyword item schema: `canonical_en`, `translation_zh`, `weight`, `confidence`, `source`, `evidence`, `aliases`, `suggested_status`.

- [ ] **Step 1: Write failing schema, metadata-filter and block tests**

Assert the parser rejects empty translations, clamps weight to 1..99 for unlocked terms, filters filenames/DOIs/page headers, and drops any canonical or alias key present in `blocked_terms`.

- [ ] **Step 2: Run the AI contract tests and observe legacy string-list failures**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_profile_ai_contracts.py tests/test_ai_contracts_v11.py -q
```

- [ ] **Step 3: Update prompts to return structured JSON only**

Request research object, method, data, region and domain concepts; require English canonical term plus Chinese translation; instruct the model to merge aliases and avoid file metadata. Do not ask the model to overwrite the whole profile.

- [ ] **Step 4: Validate locally before profile reconciliation**

Parse through one strict helper, apply canonicalization, translation length bounds, source/evidence bounds, confidence enum and blocked-alias filtering. Invalid items enter neither active nor pending lists.

- [ ] **Step 5: Emit deterministic progress stages**

Use 15 for request preparation, 35 for OCR cleanup, 70 for concept extraction, 90 for local reconciliation and 100 for completion. Failure must emit a terminal failed state through the worker.

- [ ] **Step 6: Run all AI/profile contract tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_profile_ai_contracts.py tests/test_ai_contracts_v11.py tests/test_research_profile_v11.py -q
```

- [ ] **Step 7: Record the task checkpoint**

Save one redacted example result containing invented terms only.

---

### Task 7: Redesign The Keyword Workbench And Add Native Motion/Icon Foundations

**Files:**
- Create: `ui/motion.py`
- Create: `ui/icons.py`
- Add: `assets/icons/lucide/*.svg`
- Add: `assets/icons/lucide/LICENSE`
- Modify: `ui/research_profile_dialog.py`
- Modify: `ui/frontier_settings_dialog.py`
- Modify: `ui/theme.py`
- Create: `tests/test_v12_phase1_ui.py`
- Create: `scripts/render_v12_ui.py`

**Interfaces:**
- Produces: `lucide_icon(name: str, color: QColor | str | None = None) -> QIcon`.
- Produces: `animate_widget_enter(widget: QWidget, *, distance: int = 4, duration_ms: int = 170) -> None`.
- Produces: `motion_enabled() -> bool`.
- UI object names: `profileActiveTab`, `profilePendingTab`, `profileExcludedTab`, `profileTermRow`, `profileEnglishEdit`, `profileTranslationEdit`, `profileWeight`, `profileSource`, `profileLockButton`, `profileDeleteButton`, `profileUndoButton`, `profileOcrProgress`.

- [ ] **Step 1: Write failing 1024x768 and row-geometry tests**

Instantiate the dialog offscreen with long English terms and long Chinese translations. Assert minimum design size, three tabs, descending active weights, editable translations, fixed action-track width, visible Save/Cancel, and no child geometry outside its viewport.

- [ ] **Step 2: Run the UI tests and confirm the old card layout fails**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_phase1_ui.py -q
```

- [ ] **Step 3: Add a minimal Lucide loader and selected official assets**

Vendor only icons used in v12 phase one: `upload`, `refresh-cw`, `lock`, `unlock`, `trash-2`, `undo-2`, `check`, `x`, `chevron-down`, `external-link`. Preserve the Lucide ISC license and package paths.

- [ ] **Step 4: Add a small Qt motion helper**

Use `QGraphicsOpacityEffect` plus a 4px position offset. Track animations on the widget so a second call stops the first. Return immediately when motion is disabled. Do not animate layout size.

- [ ] **Step 5: Rebuild the dialog as a dense tool surface**

Use one header, a freshness/change strip, three tabs, row-based term editors and a fixed footer. Avoid nested cards and explanatory paragraphs. The excluded tab provides per-row edit/delete/lock instead of a single comma string.

- [ ] **Step 6: Wire OCR and AI stages to the shared progress panel**

Show page `n/N`, OCR warning count, AI cleanup, extraction and reconcile states. Cancel stops workers and leaves the dialog open with the prior profile.

- [ ] **Step 7: Apply v12 typography and state tokens**

Use 18/13/12/10px type hierarchy, 6/5px radius system, semantic status colors and theme tokens. Remove oversized labels and large decorative cards.

- [ ] **Step 8: Run phase-one UI, theme and prior layout tests**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_phase1_ui.py tests/test_v115_frontier_settings_ui.py tests/test_theme_settings_v11.py tests/test_v115_cross_page_ui.py -q
```

- [ ] **Step 9: Render screenshot evidence without Computer Use**

`scripts/render_v12_ui.py` must render all themes at 1024x768 and the main widget at 400x480/480x720 into `docs/qa/v12-phase1/screenshots/`, then emit a JSON geometry report with clipped/overlapping control findings.

- [ ] **Step 10: Record the task checkpoint**

List screenshot count, geometry failures and the final zero-failure assertion.

---

### Task 8: Phase-One Data And UI Gate

**Files:**
- Create/Finalize: `docs/qa/v12-phase1-acceptance.md`
- No production code changes unless a failing check identifies a root cause.

**Interfaces:**
- Produces: a signed-off phase report containing commands, pass counts, fixture hashes, screenshots and known residual risks.

- [ ] **Step 1: Run the complete non-theme phase-one suite**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest tests/test_v12_widget_only.py tests/test_v12_storage_bootstrap.py tests/test_v12_research_profile.py tests/test_v12_profile_repository.py tests/test_v12_pdf_ocr.py tests/test_v12_profile_ai_contracts.py tests/test_v12_phase1_ui.py tests/test_research_profile_v11.py tests/test_data_root_and_legacy_safety.py tests/test_system_integration_v11.py -q
```

- [ ] **Step 2: Run theme tests separately**

```powershell
$env:QT_QPA_PLATFORM='offscreen'; & 'S:\Python\Scripts\python.exe' -m pytest tests/test_theme_settings_v11.py tests/test_v12_phase1_ui.py -q
```

- [ ] **Step 3: Run the 20-reload semantic persistence check**

Use a copied temporary `frontier.json`, migrate it, delete one former legacy keyword, save/reload 20 times, and assert active/pending/excluded/blocked IDs and translations remain stable.

- [ ] **Step 4: Run generated OCR smoke tests twice**

The second run must reuse page cache and produce the same text hash while doing no new OCR inference.

- [ ] **Step 5: Review all phase-one screenshots and geometry reports**

Reject the phase for any clipped text, oversized label, missing border, invisible focus, unreachable action, blank panel or card-inside-card layout.

- [ ] **Step 6: Finalize the phase report**

Record exact pass counts, hashes and screenshot inventory. State explicitly that no installer was built and no formal data was modified.

- [ ] **Step 7: Update the execution checklist**

Only after every phase-one check passes, mark phase one complete and begin `docs/plans/2026-08-31-v12-phase2-frontier-selection.md`.
