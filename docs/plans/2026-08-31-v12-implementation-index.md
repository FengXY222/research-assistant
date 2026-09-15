# 科研助手 v12.0 Implementation Index

## Outcome

v12.0 将只保留小组件模式，在共享科研智能核心上完成扫描 PDF OCR、长期/短期研究画像、每日前沿、证据驱动选刊和特刊征稿。三个阶段分别通过数据与 UI 验收，最后只构建和安装一个正式版本。

## Execution Order

1. [阶段一：关键词与 OCR](./2026-08-31-v12-phase1-keywords-ocr.md)
2. [阶段二：每日前沿与选刊](./2026-08-31-v12-phase2-frontier-selection.md)
3. [阶段三：LIBRARY 特刊征稿](./2026-08-31-v12-phase3-special-issues.md)
4. [最终发布、安装与数据核验](./2026-08-31-v12-release-install.md)

每一阶段只在 `S:\小软件` 开发目录运行。上一阶段的 `docs/qa/v12-phase*-acceptance.md` 为 PASS 后才进入下一阶段；中间阶段不得构建或安装。

## Product Contract

- 主窗口仅有小组件模式，最小验收尺寸 `400x480`，常用验收尺寸 `480x720`。
- HOME、WORK、PAPERS、LIBRARY 保留；LIBRARY 默认“每日前沿”，并列“期刊库”和“特刊征稿”。
- 研究画像、为论文选刊和特刊工作台使用独立 `1024x768` 窗口。
- PAPERS 保留选刊快捷入口并自动带入当前论文；不新增顶层 SUBMIT。
- 所有 AI/OCR/网络批处理都有进度、取消、失败与重试状态，且不得冻结界面。
- 正式个人数据迁移到 `%LOCALAPPDATA%\科研助手\UserData`；安装目录只放程序资源。
- 每个行为变更先写并运行失败测试，再写最小实现；所有 UI 用 Qt offscreen/QTest、截图、几何和像素检查，不使用 computer control。

## Design Skill Application

- Taste Skill v2: 采用桌面工具适用的层级、完整状态、反模板化、间距与审美审计；不引入其网页框架要求。
- GSAP Skills: 采用运动编排、可中断和性能原则；运行时仍为 Qt `QPropertyAnimation`/`QVariantAnimation`，不引入 JavaScript。
- Ponytail: 优先复用现有 PySide6 结构，处理根因，避免建立第二套 UI 或动画框架，且不以精简为由删除数据保护和错误状态。

Installed sources at planning time:

- `Leonxlnx/taste-skill` commit `ccbc15639c97057cbfcf32ecebc38ef716e4bb37`
- `greensock/gsap-skills` commit `aed9cfd3277740755f6bfc1155c7aa645403b760`
- `DietrichGebert/ponytail` commit `2ed6c52c9d7e5e56942508591085fd45dea277d3`

## Phase Gates

| Gate | Required evidence |
|---|---|
| Phase 1 | widget-only routing, stable data bootstrap, 20 reloads without keyword resurrection, lock/block boundaries, text PDF and scan PDF OCR, daily organization rollback, all-theme 1024x768 research-workbench audit |
| Phase 2 | evidence-cache integrity, deterministic signal learning, JCR Q1/Q2 frontier rule, preprint separation, topic fallback explanation, verified journal identity, hard-gate matrix, exact result-row action binding, all-theme widget/workbench audit |
| Phase 3 | source normalization, type exclusions, official/aggregator verification state, global/per-paper match, unknown-last filtering, transaction rollback, reminder schedule, widget summary and 1024x768 workbench audit |
| Release | complete regression tests, all-theme/size UI audit, redacted formal-data backup manifest, one installer, installed startup/close smoke, before/after hashes and semantic IDs, redacted final archive |

## Release Stop Conditions

Stop before installation when any test, theme, required screenshot, migration dry run, package-content check or formal backup comparison fails. Stop after installation and restore the previous data-location pointer when any existing record or stable ID is missing, the copy hashes differ, the installed version is not 12.0, or the application cannot complete a normal startup and close. Never delete the old data directory or release backup during v12.0 publication.
