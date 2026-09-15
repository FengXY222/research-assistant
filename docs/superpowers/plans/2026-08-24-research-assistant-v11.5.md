# 科研助手 v11.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏 v11.2 数据和安装升级路径的前提下，完成小组件优先的每日前沿大模块、研究设置持久化和 AI 主导选刊工作台。

**Architecture:** 继续沿用 PySide6 + 本地 JSON + 异步 worker。数据规范化集中在 `utils/file_manager.py` 与研究服务层，前沿设置通过独立的大型对话/工作台组件进入，列表页只负责阅读和反馈；选刊继续使用现有 `journal_selection_service.py`，扩展为多选分区、OA/费用口径和至少五条 AI 结果。

**Tech Stack:** Python 3.14、PySide6、pytest、PyInstaller、Inno Setup、现有 EasyScholar/DeepSeek 服务封装。

**Spec:** `docs/superpowers/specs/2026-08-24-research-assistant-v11.5-spec.md`

## Global Constraints

- 正式数据只读备份后再打包；任何升级步骤不得删除或覆盖正式 `data`。
- 新字段必须可选、可迁移、原子写入；解析失败不得清空旧 JSON。
- 小组件模式优先；不得用大模块改坏 380–540px 窄窗口。
- AI 不得静默写入锁定词、JCR/CAS 事实、投稿记录或期刊库。
- 每次功能修改先写失败测试，确认 RED 后再实现。

### Task 1: 冻结 v11.5 数据契约

**Files:**
- Modify: `utils/file_manager.py`
- Modify: `utils/research_profile_service.py`
- Test: `tests/test_v115_data_contracts.py`

- [ ] 写测试：旧画像迁移到 `terms[]`，锁定词权重变为 100，待确认词和更新日志可保存且可回读。
- [ ] 写测试：LIBRARY 默认路由设置缺失时回退到 `frontier`，不覆盖用户已有模式设置。
- [ ] 运行测试确认 RED。
- [ ] 实现规范化和兼容回退。
- [ ] 运行新增测试与现有数据安全测试。

### Task 2: 每日前沿质量门槛与显示模型

**Files:**
- Modify: `utils/frontier_service.py`
- Modify: `utils/frontier_scoring.py`
- Modify: `utils/journal_health_service.py`
- Test: `tests/test_v115_frontier_quality.py`

- [ ] 写测试：EasyScholar 核对完成后默认推荐只包含 JCR Q1/Q2；未知数据进入待核验；Q3/Q4 被过滤。
- [ ] 写测试：JCR 与中科院分区格式化为短标签，缺失状态有明确文本。
- [ ] 运行 RED。
- [ ] 实现质量门槛、去重和统一 metric line。
- [ ] 运行前沿与期刊库现有测试。

### Task 3: 小组件优先的每日前沿大模块

**Files:**
- Create: `ui/frontier_settings_dialog.py`
- Modify: `ui/frontier_page.py`
- Modify: `ui/main_window.py`
- Modify: `ui/theme.py`
- Test: `tests/test_v115_frontier_settings_ui.py`

- [ ] 写离屏 UI 测试：研究设置入口可见，默认前沿页可见，锁定/待确认/来源/分区/更新策略区域存在。
- [ ] 写窄窗口测试：380/460/540px 无横向溢出，按钮和标题不遮挡。
- [ ] 运行 RED。
- [ ] 实现大模块工作台：关键词锁定、手动补词、论文/PDF 提取入口、JCR/CAS/OA 过滤、更新策略、保存/取消。
- [ ] 将小组件默认展示收缩为摘要与高频动作，点击设置进入大模块。
- [ ] 运行 UI 契约和主题测试。

### Task 4: PAPERS AI 选刊工作台

**Files:**
- Modify: `utils/journal_selection_service.py`
- Modify: `ui/journal_selection_dialog.py`
- Modify: `utils/ai_service.py`
- Test: `tests/test_v115_journal_selection.py`

- [ ] 写测试：出版社、费用、JCR/CAS 多选约束归一化；混合 OA 同时满足两种费用筛选。
- [ ] 写测试：AI 结果不足五条时保留本地回退并明确状态；失败时页面不空白。
- [ ] 写测试：外部候选快速入库后可加入投稿路径，旧投稿历史不变。
- [ ] 运行 RED。
- [ ] 实现 AI 主推荐状态、至少五条结果、AI 总分、理由、风险、OA/分区核对和快速入库。
- [ ] 在 PAPERS 入口接入完整工作台，保持拒稿归档为角落入口。
- [ ] 运行全部选刊与论文测试。

### Task 5: HOME/WORK/LIBRARY 细节回归

**Files:**
- Modify: `ui/home_page.py`
- Modify: `ui/todo_page.py`
- Modify: `ui/notes_page.py`
- Modify: `ui/journal_library_page.py`
- Test: `tests/test_v115_cross_page_ui.py`

- [ ] 写测试：HOME 今日下一步可定位并闪烁对应论文/期刊；任务、下一步、最近节点不重复。
- [ ] 写测试：WORK 优先级拖动覆盖层文字和按钮在小组件尺寸可读。
- [ ] 写测试：期刊名可直接复制，中科院分区完整显示不变成省略号。
- [ ] 运行 RED。
- [ ] 实现跨页联动、布局密度和直接复制。
- [ ] 运行 v11.2 UI 回归。

### Task 6: 全量验证、数据审计和 v11.5 打包

**Files:**
- Modify: `utils/app_info.py`
- Modify: `build_windows.ps1`
- Modify: `科研助手.iss`
- Modify: `科研助手.spec`
- Create: `docs/qa/v11.5-final-acceptance.md`
- Create: `发布/v11.5-final-qa-<timestamp>/`

- [ ] 运行 `compileall`、全量 pytest、离屏尺寸矩阵和应用自检。
- [ ] 备份正式安装目录数据，生成逐文件 SHA-256 清单。
- [ ] 更新版本到 11.5.0，构建 PyInstaller 和 Inno Setup 安装包。
- [ ] 安装到正式路径，核对安装前后数据哈希、记录数量和 PDF 链接。
- [ ] 启动安装后的程序，验证小组件默认模式、每日前沿默认页、主题切换和关键点击反馈。
- [ ] 只有全部证据保存后，才标记计划完成。
