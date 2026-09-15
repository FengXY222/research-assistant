# 科研助手 v12.0 最终发布、安装与数据核验 Implementation Plan

> **For agentic workers:** REQUIRED SKILLS: use `test-driven-development` for release-identity and migration behavior, `systematic-debugging` for any build/install failure, and `verification-before-completion` before every release claim. Execute inline only after all three phase reports pass. Do not install an intermediate build.

**Goal:** 在完整功能与 UI 验收通过后，只生成并安装一个 v12.0 正式安装包，并以文件哈希、记录数量和关键 ID 证明个人正式数据未丢失。

**Architecture:** 发布流程将正式数据与可重建缓存严格分开。安装前对当前正式数据建立只读清单和备份，安装包不包含或覆盖 `UserData`；首次启动由阶段一的 bootstrap 执行复制、哈希校验、原子切换和结构迁移。安装后再次做文件级和语义级比对，任何意外变化均触发停止与回滚。

**Tech Stack:** PowerShell 7、Python 3.12、PyInstaller、Inno Setup、PySide6、pytest/unittest、SHA-256、Qt offscreen/QTest。

**Spec:** `docs/specs/2026-08-31-v12-research-intelligence-design.md`

## Global Constraints

- `docs/qa/v12-phase1-acceptance.md`、`v12-phase2-acceptance.md` 和 `v12-phase3-acceptance.md` 必须全部为通过。
- v12.0 只保留小组件模式；不得重新出现软件模式或模式选择控件。
- 只生成并安装一个 `科研助手-v12.0-安装包.exe`，不得安装阶段预览版。
- 正式数据目标目录为 `%LOCALAPPDATA%\科研助手\UserData`，安装目录不得作为默认个人数据目录。
- 安装包不得包含测试数据、开发缓存、真实 API Key 或用户数据。
- 所有哈希使用 SHA-256；正式数据报告不得输出 API Key 的内容或哈希。
- OCR 运行库、模型及许可证必须随安装包提供，用户无需另装软件。
- 所有 Python 命令使用 `S:\Python\Scripts\python.exe`，PowerShell 命令使用 `pwsh -NoLogo -NoProfile`。
- 当前目录不是 Git 仓库；发布以自动化报告、安装包哈希和归档清单为审计记录。

## File Map

- Modify `utils/app_info.py`: 将正式版本更新为 `12.0`。
- Modify `build_windows.ps1`: 打包 OCR 运行库、模型、图标和许可证并固定产物名。
- Modify `科研助手.spec`: 声明隐藏导入、二进制与数据文件。
- Modify `科研助手.iss`: 仅安装程序资源，保护稳定 `UserData`。
- Modify `version_info.txt`: 同步 Windows 文件版本和产品版本。
- Modify `README.md`: 更新 v12 功能、数据位置和恢复说明。
- Create `docs/科研助手-v12.0-使用说明.md`: 非技术用户使用手册。
- Modify `tests/test_release_identity_v11.py`: 升级为 v12 发布身份断言。
- Create `tests/test_v12_package_contents.py`: 安装包输入清单与敏感数据排除测试。
- Create `scripts/formal_data_manifest.py`: 生成脱敏文件/语义清单并核对两个清单。
- Create `scripts/release_gate_v12.py`: 汇总阶段报告、测试报告、UI 报告和包清单。
- Create `docs/qa/v12-final-ui-audit.md`: 最终主题和尺寸核验报告。
- Create `docs/qa/v12-release-verification.md`: 构建、安装、启动和正式数据核验报告。
- Create final archive under `发布/v12.0-final-<timestamp>/` only after every gate passes.

---

### Task 1: Run The Final Functional And UI Gate

**Files:**
- Read: `docs/qa/v12-phase1-acceptance.md`
- Read: `docs/qa/v12-phase2-acceptance.md`
- Read: `docs/qa/v12-phase3-acceptance.md`
- Modify: `scripts/render_v12_ui.py`
- Create: `scripts/release_gate_v12.py`
- Create: `tests/test_v12_final_ui_contract.py`
- Create: `docs/qa/v12-final-ui-audit.md`

**Interfaces:**
- Consumes: phase acceptance reports with `status: PASS`.
- Produces: `verify_phase_reports(paths: list[Path]) -> list[str]`, returning blocking failures.
- Produces: `audit_widget_capture(path: Path, *, width: int, height: int) -> dict[str, object]`.
- Produces: `docs/qa/v12-final-ui-audit.md` with each theme/size/window marked PASS or FAIL.

- [ ] **Step 1: Write failing release-gate tests**

```python
def test_release_gate_rejects_missing_or_failed_phase(tmp_path):
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    good.write_text("status: PASS", encoding="utf-8")
    bad.write_text("status: FAIL", encoding="utf-8")
    failures = verify_phase_reports([good, bad, tmp_path / "missing.md"])
    assert len(failures) == 2

def test_final_ui_matrix_has_every_required_capture():
    matrix = required_capture_matrix()
    assert {item["size"] for item in matrix} >= {(400, 480), (480, 720), (1024, 768)}
    assert {item["theme"] for item in matrix} == set(available_theme_ids())
```

- [ ] **Step 2: Verify the tests fail for the missing gate and matrix**

Run:

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest 'S:\小软件\tests\test_v12_final_ui_contract.py' -v
```

Expected: FAIL because `release_gate_v12` and the complete matrix do not exist.

- [ ] **Step 3: Implement the report parser and capture matrix**

The matrix must render:

```python
WIDGET_SIZES = [(400, 480), (480, 720)]
WORKBENCH_SIZE = (1024, 768)
WIDGET_PAGES = ["home", "work", "papers", "frontier", "journals", "special_issues"]
WORKBENCHES = ["research_profile", "journal_selection", "special_issue"]
STATES = ["normal", "empty", "loading", "error", "long_text"]
```

Use `QT_QPA_PLATFORM=offscreen`, `QWidget.grab()`, layout geometry and sampled pixels. Fail for clipped controls, intersecting visible siblings, text outside its content rectangle, blank captures, missing borders, font sizes outside component tokens, unreachable primary actions, or missing progress during an AI/OCR state. Do not use computer control.

- [ ] **Step 4: Run the full automated regression suites**

Run the non-theme suite first, then the theme/UI suite in a fresh process:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
& 'S:\Python\Scripts\python.exe' -m pytest 'S:\小软件\tests' -q --ignore='S:\小软件\tests\test_theme_switching.py'
& 'S:\Python\Scripts\python.exe' -m pytest 'S:\小软件\tests\test_theme_switching.py' -vv -x
```

Expected: both commands exit 0 with zero failures.

- [ ] **Step 5: Render and audit every required UI state**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
& 'S:\Python\Scripts\python.exe' 'S:\小软件\scripts\render_v12_ui.py' --all-themes --all-states --output 'S:\小软件\artifacts\v12-final-ui'
& 'S:\Python\Scripts\python.exe' 'S:\小软件\scripts\release_gate_v12.py' ui --captures 'S:\小软件\artifacts\v12-final-ui' --report 'S:\小软件\docs\qa\v12-final-ui-audit.md'
```

Expected: report status PASS; no software-mode capture exists; 400x480 is the primary inspection target and 1024x768 tools remain fully reachable.

---

### Task 2: Build A Redacted Formal-Data Manifest And Backup

**Files:**
- Create: `scripts/formal_data_manifest.py`
- Create: `tests/test_v12_formal_data_manifest.py`
- Create at runtime: `artifacts/v12-release/formal-before.json`
- Create at runtime: `artifacts/v12-release/formal-backup/`

**Interfaces:**
- Produces: `build_manifest(root: Path, *, redact_names: set[str]) -> dict[str, object]`.
- Produces: `compare_manifests(before: dict, after: dict, *, allow_schema_files: set[str]) -> list[str]`.
- Produces: manifest fields `relative_path`, `size`, `sha256`, `record_count`, and stable record IDs.
- Never emits secret values or hashes from settings fields matching `api_key`, `token`, `secret`, or `password`.

- [ ] **Step 1: Write failing redaction and semantic-comparison tests**

```python
def test_manifest_redacts_secret_values_but_keeps_nonsecret_counts(tmp_path):
    (tmp_path / "settings.json").write_text(
        '{"easyscholar_api_key":"private","theme":"forest"}', encoding="utf-8"
    )
    manifest = build_manifest(tmp_path, redact_names=DEFAULT_SECRET_KEYS)
    payload = json.dumps(manifest, ensure_ascii=False)
    assert "private" not in payload
    assert "easyscholar_api_key" not in payload
    assert manifest["files"][0]["record_count"] == 2

def test_manifest_comparison_reports_missing_stable_ids():
    failures = compare_manifests(before_with_ids(["p1", "p2"]), after_with_ids(["p1"]), allow_schema_files=set())
    assert failures == ["papers.json: missing id p2"]
```

- [ ] **Step 2: Run tests and confirm the expected failure**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest 'S:\小软件\tests\test_v12_formal_data_manifest.py' -v
```

- [ ] **Step 3: Implement deterministic manifesting**

Sort paths and stable IDs, stream file hashing in 1 MiB blocks, parse JSON with structured APIs, count top-level list/dict records, and extract existing `id`, `paper_id`, `journal_id`, `task_id`, `issue_id`, or `uuid` values. For settings, retain only nonsecret key names and counts. Attachment audit records existence and size but never rewrites missing paths.

- [ ] **Step 4: Locate the current formal data without mutating it**

Candidates must be checked in this order:

1. Explicit configured data directory.
2. `%LOCALAPPDATA%\科研助手\UserData`.
3. `%LOCALAPPDATA%\Programs\科研助手\data`.
4. Known historical installation directories returned by `storage_bootstrap.discover_legacy_roots()`.

Select the richest valid dataset using the same deterministic score as the application bootstrap. Log only paths and aggregate counts.

- [ ] **Step 5: Stop the application, create the before manifest, then copy a backup**

Use PowerShell process APIs to close only an exact `科研助手.exe` process, wait for exit, and never terminate unrelated processes. Copy with `Copy-Item -LiteralPath` into `artifacts/v12-release/formal-backup`, then build a second manifest for the backup.

```powershell
& 'S:\Python\Scripts\python.exe' 'S:\小软件\scripts\formal_data_manifest.py' create --root '<resolved-formal-root>' --output 'S:\小软件\artifacts\v12-release\formal-before.json'
& 'S:\Python\Scripts\python.exe' 'S:\小软件\scripts\formal_data_manifest.py' compare --before 'S:\小软件\artifacts\v12-release\formal-before.json' --root 'S:\小软件\artifacts\v12-release\formal-backup'
```

Expected: exact file hash and semantic match. Any mismatch blocks the build.

- [ ] **Step 6: Record pre-existing attachment problems without changing data**

The report must list missing attachment paths as pre-existing observations. In particular, re-check rather than assume the prior missing link `C:/Users/fxy17/OneDrive/已发表论文专利/CN202310556010.pdf`; do not delete or repair its record during release.

---

### Task 3: Freeze v12 Identity, Dependencies, Licenses, And Documentation

**Files:**
- Modify: `utils/app_info.py`
- Modify: `build_windows.ps1`
- Modify: `科研助手.spec`
- Modify: `科研助手.iss`
- Modify: `version_info.txt`
- Modify: `README.md`
- Create: `docs/科研助手-v12.0-使用说明.md`
- Modify: `tests/test_release_identity_v11.py`
- Create: `tests/test_v12_package_contents.py`

**Interfaces:**
- Produces: application semantic version `12.0` and Windows file version `12.0.0.0`.
- Produces: installer path `dist/科研助手-v12.0-安装包.exe`.
- Produces: packaged dependency/license inventory consumed by the package-content test.

- [ ] **Step 1: Write failing identity and package-content tests**

```python
def test_release_identity_is_v12():
    assert APP_VERSION == "12.0"
    assert "科研助手-v12.0-安装包.exe" in Path("科研助手.iss").read_text(encoding="utf-8-sig")

def test_package_inputs_include_ocr_runtime_and_licenses():
    inventory = package_inventory()
    assert inventory.has_module("pypdfium2")
    assert inventory.has_module("rapidocr_onnxruntime")
    assert inventory.has_module("onnxruntime")
    assert inventory.has_license_for("PDFium")
    assert inventory.has_license_for("RapidOCR")
    assert inventory.has_license_for("ONNX Runtime")
    assert not inventory.contains_user_data()
```

- [ ] **Step 2: Run the tests and confirm they fail against v11.5.7**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest 'S:\小软件\tests\test_release_identity_v11.py' 'S:\小软件\tests\test_v12_package_contents.py' -v
```

- [ ] **Step 3: Update every release identity atomically**

Set `APP_VERSION = "12.0"`, Windows numeric versions to `12,0,0,0`, product text to `12.0`, and output filename to `科研助手-v12.0-安装包.exe`. The Inno script must not create, delete, replace, or clean `%LOCALAPPDATA%\科研助手\UserData`.

- [ ] **Step 4: Bundle local OCR dependencies and license texts**

Use PyInstaller collection helpers for `pypdfium2`, `rapidocr_onnxruntime`, and `onnxruntime`. Include only the selected RapidOCR models, PDFium binary, Lucide subset and third-party license files. Exclude tests, caches, downloaded source repositories, sample PDFs and any `data/*.json` personal dataset.

- [ ] **Step 5: Write the nontechnical v12 user guide**

The guide must cover: widget navigation, research profile regions, scan-PDF OCR, daily organization and same-day undo, frontier journal/preprint split, one-line feedback, evidence-based journal selection, special-issue widget/workbench, progress/cancel/retry states, stable data directory, backup/restore, EasyScholar behavior, and privacy. It must explicitly say that no extra OCR software is required.

- [ ] **Step 6: Re-run identity and inventory tests**

```powershell
& 'S:\Python\Scripts\python.exe' -m pytest 'S:\小软件\tests\test_release_identity_v11.py' 'S:\小软件\tests\test_v12_package_contents.py' -v
```

Expected: PASS.

---

### Task 4: Build And Inspect The Single Final Installer

**Files:**
- Read/execute: `build_windows.ps1`
- Create at runtime: `dist/科研助手-v12.0-安装包.exe`
- Create at runtime: `artifacts/v12-release/package-manifest.json`

**Interfaces:**
- Consumes: passing final UI gate, before-data manifest, frozen release identity and package inventory.
- Produces: one final installer and its SHA-256, size and Windows version metadata.

- [ ] **Step 1: Run the release gate before invoking a build tool**

```powershell
& 'S:\Python\Scripts\python.exe' 'S:\小软件\scripts\release_gate_v12.py' prebuild --root 'S:\小软件' --report 'S:\小软件\artifacts\v12-release\prebuild-gate.json'
```

Expected: PASS. A failed phase/UI/test/data gate exits nonzero and prevents building.

- [ ] **Step 2: Build once with enough timeout**

```powershell
pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File 'S:\小软件\build_windows.ps1'
```

Expected: exit 0 and exactly one v12 installer in `dist`.

- [ ] **Step 3: Inspect the frozen app before installation**

Run release-identity, import, startup and package-content smoke checks against the build output. Verify the executable opens under an isolated temporary `RESEARCH_ASSISTANT_DATA_DIR`, shows widget navigation only, imports OCR modules, opens each 1024x768 workbench, and closes without modifying the fixture.

- [ ] **Step 4: Hash and inventory the installer**

```powershell
Get-FileHash -LiteralPath 'S:\小软件\dist\科研助手-v12.0-安装包.exe' -Algorithm SHA256
```

Write the exact digest, file size, build timestamp, application version, dependency inventory and license inventory to `artifacts/v12-release/package-manifest.json`. Do not include user data or secrets.

---

### Task 5: Install v12.0 And Prove Formal-Data Preservation

**Files:**
- Consume: `artifacts/v12-release/formal-before.json`
- Consume: `dist/科研助手-v12.0-安装包.exe`
- Create: `artifacts/v12-release/formal-after-copy.json`
- Create: `artifacts/v12-release/formal-after-startup.json`
- Create: `docs/qa/v12-release-verification.md`

**Interfaces:**
- Consumes: `storage_bootstrap.bootstrap_user_data()` from phase one.
- Produces: exact pre-schema copy match and semantic post-migration match.
- Produces: rollback evidence if any unexpected mismatch occurs.

- [ ] **Step 1: Rebuild the before manifest immediately before installation**

Close the exact app process, regenerate `formal-before.json`, compare it with the backup, and verify the installer hash still equals `package-manifest.json`. Any drift requires a new backup and review before continuing.

- [ ] **Step 2: Perform the one permitted formal installation**

Launch `科研助手-v12.0-安装包.exe` silently with the existing install location. Wait for the installer process to exit and verify Windows reports version 12.0. Do not manually copy JSON into the program directory.

- [ ] **Step 3: Verify copy integrity before schema migration**

The bootstrap migration journal must contain the source root, target root, file list and source/target SHA-256 values. Require every copied personal file to match exactly before the atomic location switch. The original directory and the release backup remain untouched.

- [ ] **Step 4: Launch and close the installed application once**

Start the installed `科研助手.exe`, wait until the main widget reports ready, verify 400x480 layout and widget-only routing using the in-app diagnostic hook, then request a normal close. Do not use computer control. Capture startup/close exit status and diagnostic JSON.

- [ ] **Step 5: Compare the post-startup formal data semantically**

```powershell
& 'S:\Python\Scripts\python.exe' 'S:\小软件\scripts\formal_data_manifest.py' create --root "$env:LOCALAPPDATA\科研助手\UserData" --output 'S:\小软件\artifacts\v12-release\formal-after-startup.json'
& 'S:\Python\Scripts\python.exe' 'S:\小软件\scripts\formal_data_manifest.py' compare --before 'S:\小软件\artifacts\v12-release\formal-before.json' --after 'S:\小软件\artifacts\v12-release\formal-after-startup.json' --allow-v12-migration
```

Expected: all pre-v12 personal records and stable IDs remain; legacy file hashes are accounted for by either an exact unchanged copy or a deterministic migrated representation. New v12 files may be added, but no old record may disappear. OCR/evidence caches are excluded from personal-data equality.

- [ ] **Step 6: Execute the rollback path if any comparison fails**

Stop the installed app, preserve the failed target for diagnosis, atomically restore the prior data-location pointer and original directory, and do not report release success. Never delete the backup. Record the mismatch and rollback outcome in the release report.

- [ ] **Step 7: Write the formal verification report**

Include installer/executable SHA-256, version, test counts, UI matrix count, formal root before/after, per-file status, aggregate record counts, missing attachment observations, migration status, startup/close result and final PASS/FAIL. Redact secret keys and values.

---

### Task 6: Archive The Auditable Final Release

**Files:**
- Create: `发布/v12.0-final-<timestamp>/release-manifest.json`
- Copy: installer, user guide, three phase reports, final UI audit and release verification report.

**Interfaces:**
- Produces: an immutable release folder whose manifest hashes every included artifact.
- Produces: no copy of `%LOCALAPPDATA%\科研助手\UserData`, API keys, personal paper files or OCR text.

- [ ] **Step 1: Require every final gate to be PASS**

`release_gate_v12.py final` must verify the three phase reports, full tests, theme tests, final UI audit, package manifest, installed version, installer hash, startup smoke and formal-data comparison. Missing evidence is a failure, not a warning.

- [ ] **Step 2: Create the timestamped archive with native PowerShell file APIs**

Resolve and verify that the target stays inside `S:\小软件\发布` before copying. Copy only the installer and redacted documentation/reports. Do not recursively copy build directories.

- [ ] **Step 3: Hash every archived artifact and write the manifest**

The manifest contains relative path, byte size, SHA-256, application version, build time and report statuses. Re-hash the archived installer and require it to equal the installed package manifest.

- [ ] **Step 4: Run one final read-only verification**

```powershell
& 'S:\Python\Scripts\python.exe' 'S:\小软件\scripts\release_gate_v12.py' final --archive '<resolved-v12-final-directory>' --report 'S:\小软件\docs\qa\v12-release-verification.md'
```

Expected: PASS and zero mutations to the formal data manifest. Only after this fresh command may v12.0 be reported as built, installed and data-preserving.
