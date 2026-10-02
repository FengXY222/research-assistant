# SQLite 业务数据架构

## 边界

SQLite 按数据寿命拆分为两个物理数据库：

- `research_assistant.sqlite`：不可随意删除的用户业务数据和 `app_*` 状态；
- `research_intelligence.sqlite`：可重建的网络响应、论文发现与核验证据缓存。

小型界面配置仍保存在 JSON。每日前沿、期刊库、特刊、去重指纹、来源运行状态、用户操作记录和任务批次以 SQLite 为准。迁移后的同名大型 JSON 仅是指向归档的标记，不再参与读取或写入。

## 主要业务表

| 数据 | 表 |
|---|---|
| 每日前沿论文及预计算评分 | `app_frontier_articles` |
| 期刊资料和指标 | `app_journals` |
| 特刊标准化记录 | `app_special_issues` |
| 特刊来源证据 | `app_special_issue_sources` |
| 特刊征稿范围 | `app_special_issue_scopes` |
| 特刊个人状态 | `app_special_issue_user_state` |
| 特刊候选截止日期 | `app_special_deadline_candidates` |
| 来源运行状态 | `app_source_runs` |
| 去重指纹 | `app_dedupe_fingerprints` |
| 用户事件 | `app_user_events` |
| 可续跑任务批次 | `app_task_runs` |

候选截止日期按“日期＋来源”合并，不因抓取时间变化而新增记录；保留首次和末次发现时间以及最新完整证据，每个特刊最多保留10组不同候选。

## 线程和界面约束

- SQLite开启 WAL、外键、`busy_timeout`和`NORMAL`同步模式；
- 每个线程建立自己的短连接；
- 数据采集、AI评分、特刊操作和页面重载不在GUI线程执行；
- GUI立即显示现有模型，后台返回后再替换或增量更新；
- 期刊库使用`QAbstractListModel + QListView + QStyledItemDelegate`，每批40条，不创建逐行按钮和菜单；
- 三个文献页面不挂载整页透明度动画；
- 备份只保存业务数据库，使用SQLite在线备份接口，不备份可重建缓存；
- 软件空闲时删除过期网络响应和已不对应当前特刊的发现缓存，在可回收空间达到阈值时再执行数据库压缩。

## 迁移与回滚

迁移工具：

```powershell
python tools/migrate_business_data_sqlite.py --data-root "<用户数据目录>"
```

工具先完成导入，再核对各数据集记录数、候选截止日期上限和数据库完整性。从旧版升级时，先把缓存库中的 `app_*` 表事务性复制到 `research_assistant.sqlite`，数量和完整性逐项通过后才从缓存库删除旧表。旧JSON移入`legacy-json-archive/<时间戳>`，活动位置留下小型迁移标记。需要人工回滚时，应先退出软件，再从该目录恢复原文件。
