"""Frozen-dataset frontier evaluation. Never invoke live storage/recommendation APIs.

--live-ai sends only the bounded profile and public candidate text to the user's
configured AI. --replay reproduces the report entirely from captured responses.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

# These reference labels are declared before any new AI evaluation. Boundary
# examples deliberately have no forced correct answer and need domain judgment.
SAMPLES = [
    ("10.1186/s12909-026-10328-1", "negative", "护理教育，非土壤研究"),
    ("10.1186/s12889-026-29111-4", "negative", "大学生健康，非研究方向"),
    ("10.1186/s12864-026-13314-5", "negative", "哺乳动物基因，非研究方向"),
    ("10.1126/sciadv.aej0987", "negative", "膜蛋白系统，mapping词义不同"),
    ("10.1126/sciadv.aed1511", "negative", "病毒遗传系统，非研究方向"),
    ("10.1021/jacs.6c11666", "negative", "有机合成碳同位素，非土壤碳"),
    ("10.1007/s10443-026-10535-5", "negative", "碳纤维材料，非土壤碳"),
    ("10.3389/fchem.2026.1921103", "negative", "电池碳材料，非土壤碳"),
    ("10.1111/jfb.70615", "negative", "鱼类长度换算，非耕地转化"),
    ("10.1057/s41599-026-08925-y", "negative", "social不是SOC"),
    ("10.1038/s41598-026-70402-y", "negative", "仅共享机器学习和carbon"),
    ("10.1021/acs.orglett.6c03083", "negative", "有机合成，检索命中不是证据"),
    ("10.1523/jneurosci.1323-25.2026", "negative", "神经科学，非研究方向"),
    ("10.1126/sciadv.aed8384", "negative", "HIV结构，非研究方向"),
    ("10.1128/spectrum.04173-25", "negative", "脑膜炎诊断，非研究方向"),
    ("10.1136/jitc-2026-015414", "negative", "SOC表示标准治疗"),
    ("10.1109/access.2026.3725118", "negative", "SoC芯片和稻米分选，非稻田土壤研究"),
    ("10.69930/fsst.v3i2.917", "negative", "soil-transmitted为寄生虫医学语境"),
    ("10.3791/30055", "negative", "DNA conversion不是土地转化"),
    ("10.1021/acs.est.6c06240", "negative", "催化材料，不能因环境期刊就通过"),
    ("10.1016/j.geoderma.2026.118004", "positive", "SOC数字制图及不确定性，明确核心"),
    ("10.1016/j.geoderma.2026.118011", "positive", "土壤C:N三维制图，明确内容联系"),
    ("10.1016/j.still.2026.107192", "positive", "SOC组分空间格局与驱动，标题有明确证据"),
    ("10.1016/j.eja.2026.128237", "positive", "SOC组分和过程建模，不能因摘要缺失一律剔除"),
    ("10.3390/agriculture16161694", "positive", "土地利用、SOC和随机森林，内容相关与分区分开"),
    ("10.3390/rs18172924", "positive", "土地变化与碳储量时空分析，可有明确联系"),
    ("10.3390/w18172183", "boundary", "水体DOC时空建模，是否接受对象迁移"),
    ("10.1057/s41599-026-08869-3", "boundary", "城市能源与可解释机器学习，迁移依据薄弱"),
    ("10.1038/s41598-026-69512-4", "boundary", "地下水缺失数据处理，缺摘要"),
    ("10.3390/w18172162", "boundary", "DEM分辨率与洪水易发性，关联已有滑坡论文"),
    ("10.1016/j.rse.2026.115644", "boundary", "树种高光谱分类，仅方法邻近"),
    ("10.5194/hess-30-5521-2026", "boundary", "过程模型结合深度学习和SHAP，水文对象迁移"),
    ("10.3389/fsufs.2026.1930186", "boundary", "土壤质量制图与soil health排除边界"),
    ("10.1038/s43247-026-03974-2", "boundary", "稻田重金属机制，当前存在heavy metal排除"),
    ("10.1016/j.ecolind.2026.115424", "boundary", "土地利用与生态风险，是否过泛"),
    ("10.1007/s00374-026-02050-3", "boundary", "土壤细菌主题与微生物排除边界"),
    ("10.34133/ehs.0559", "boundary", "侵蚀土壤碳综述与erodibility排除边界"),
    ("10.1016/j.jenvman.2026.130845", "boundary", "葡萄园地球化学分区，与用户已忽略记录对照"),
    ("10.1016/j.catena.2026.110483", "boundary", "土壤碳铁结合与mineral排除冲突"),
    ("10.1007/s11104-026-08878-w", "boundary", "SOC组分相关但主要是秸秆还田田间试验"),
]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_output(source: Path, output: Path) -> None:
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Offline output must be separate from the formal data directory")


def source_hashes(source: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.iterdir())
            if p.is_file() and (p.suffix == ".json" or p.name.startswith("research_intelligence.sqlite"))}


def prepare_snapshot(source: Path, output: Path) -> dict:
    from utils.frontier_admission import build_review_context
    frontier = read_json(source / "frontier.json")
    lookup = {str(i["id"]): i for i in frontier["items"]}
    missing = [i for i, _, _ in SAMPLES if i not in lookup]
    if missing:
        raise ValueError(f"Frozen reference items unavailable: {missing}")
    context = build_review_context(read_json(source / "research_profile.json"), read_json(source / "papers.json"))
    settings = read_json(source / "settings.json")
    es = settings.get("easyscholar", {})
    snapshot = {"created_at": datetime.now().isoformat(timespec="seconds"),
                "last_checked": frontier.get("last_checked"), "total_source_items": len(frontier["items"]),
                "reference_label_author": "助手基于标题摘要及既有需求预标注，非用户金标准",
                "reference_label_timing": "before_new_ai_review",
                "context": context, "journals": read_json(source / "journals.json"),
                "easyscholar_ready": bool(es.get("enabled") and es.get("secret_key_secret")),
                "source_hashes_at_snapshot": source_hashes(source),
                "samples": [{"expected_group": group, "reference_note": note, "item": lookup[i]}
                            for i, group, note in SAMPLES]}
    write_json(output / "snapshot.json", snapshot)
    return snapshot


def review_metrics(rows: list[dict]) -> dict:
    result = {"total": len(rows), "routes": dict(Counter(r["new"]["route"] for r in rows))}
    for group in ["positive", "negative", "boundary"]:
        values = [r for r in rows if r["expected_group"] == group]
        result[f"reference_{group}_total"] = len(values)
        for decision in ["accept", "reject", "pending"]:
            key = {"accept": "accepted", "reject": "rejected", "pending": "pending"}[decision]
            result[f"reference_{group}_{key}"] = sum(r["new"]["content_decision"] == decision for r in values)
    return result


def render_report(result: dict, output: Path) -> None:
    decision_names = {"accept": "内容通过", "reject": "内容淘汰", "pending": "内容待定"}
    route_names = {"journal": "期刊流合格", "rejected": "不推荐", "pending_content": "待补信息/确认",
                   "pending_quality": "分区待核验", "excluded_quality": "分区不合格", "preprint": "独立预印本区"}
    group_names = {"positive": "参考相关", "negative": "明显无关", "boundary": "研究边界"}
    m = result["metrics"]
    lines = ["# 每日前沿：第一阶段离线对照", "", f"生成时间：{result['generated_at']}。策略：{result['policy_version']}。", "",
             "本次未接入正式推荐、未清空收藏或阅读状态、未打包安装。仅冻结数据离线回放；AI判断调用已配置服务。",
             "分区仅复用现有缓存和期刊库，没有请求EasyScholar或重新抓取文献。未知分区与内容不相关分别统计。", "",
             "## 结果概览", "",
             f"- 样本40篇，来自现有{result['source_items']}篇记录。不是随机样本，不能据此宣称总体准确率。",
             f"- 参考相关 {m['reference_positive_total']} 篇：通过 {m['reference_positive_accepted']}、淘汰 {m['reference_positive_rejected']}、待定 {m['reference_positive_pending']}。",
             f"- 明显无关 {m['reference_negative_total']} 篇：淘汰 {m['reference_negative_rejected']}、误放行 {m['reference_negative_accepted']}、待定 {m['reference_negative_pending']}。",
             f"- 研究边界 {m['reference_boundary_total']} 篇：通过 {m['reference_boundary_accepted']}、淘汰 {m['reference_boundary_rejected']}、待定 {m['reference_boundary_pending']}，需要结合用户偏好解释。",
             f"- 正式数据内容哈希一致：{'是' if result['formal_data_unchanged'] else '否，详见manifest，不自动覆盖'}（{result['hashed_files']}个文件）。", "",
             "预标注由助手在新AI评估前完成，不是用户逐篇确认的金标准。AI未看到预标注、原分数、期刊名、原理由、阅读状态或针对样本的行为反馈。",
             "本次评估内容准入，不重新推送已经读过/忽略的记录。表中的‘期刊流合格’只代表内容与分区合格，不代表要恢复到今日推荐。", "",
             "## 逐篇对照", "", "| 编号 | 论文 | 参考分组 | 原分数 | 新内容结论 | 新分数 | 后续分流 |",
             "|---|---|---|---:|---|---:|---|"]
    for n, row in enumerate(result["rows"], 1):
        v = row["new"]
        title = row["title"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {n} | {title} | {group_names[row['expected_group']]} | {row['old_score']} | {decision_names[v['content_decision']]} | {v.get('score') if v.get('score') is not None else '—'} | {route_names[v['route']]} |")
    lines += ["", "## 画像冲突", "", "以下仅标记，不修改正式画像："]
    context = result["context"]
    refs = {t["id"]: t["text"] for t in context["terms"] + context["exclusions"]}
    for conflict in context["conflicts"]:
        lines.append(f"- 排除词 {refs[conflict['excluded_ref']]} 与生效词 {refs[conflict['active_ref']]} 重叠，不能字面一刀切。")
    lines += ["", "## 判断依据", ""]
    for n, row in enumerate(result["rows"], 1):
        v = row["new"]
        lines += [f"### {n}. {row['title']}", "", f"- DOI：{row['id']}；期刊：{row['journal']}；原状态：{row['old_status']}。",
                  f"- 独立参考：{row['reference_note']}。",
                  f"- 原理由：{row['old_reason'] or '未记录'}。",
                  f"- 新判断：{decision_names[v['content_decision']]}；{route_names[v['route']]}。{v['reason_cn']}",
                  f"- 原始内容证据：{' / '.join(v.get('evidence_quotes', [])) or '暂无合格引用'}。"]
        if v.get("exclusion_reason_cn"):
            lines.append(f"- 排除词判断：{v['exclusion_reason_cn']}")
        if v.get("validation_issue"):
            lines.append(f"- 程序校验：{v['validation_issue']}")
        lines.append("")
    (output / "对照报告.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--live-ai", action="store_true")
    mode.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    validate_output(source, output)
    output.mkdir(parents=True, exist_ok=True)
    # Imports may consult process-local paths; they must never see the formal root.
    os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(output / "isolated-runtime")
    before = source_hashes(source)
    snapshot_path = output / "snapshot.json"
    snapshot = read_json(snapshot_path) if snapshot_path.exists() else prepare_snapshot(source, output)
    if args.prepare:
        print(f"Frozen {len(snapshot['samples'])} samples; profile conflicts {len(snapshot['context']['conflicts'])}")
        return 0
    from utils.frontier_admission import POLICY_VERSION, REVIEW_SYSTEM, review_candidates
    cache_dir = output / "ai-cache"
    cache_dir.mkdir(exist_ok=True)
    settings = read_json(source / "settings.json")
    config = settings.get("ai", {})
    if args.live_ai and (not config.get("enabled") or not config.get("frontier_rerank")):
        raise ValueError("Configured frontier AI is not enabled; no remote requests made")
    key = ""
    if args.live_ai:
        from utils.secure_store import reveal_secret
        key = reveal_secret(config.get("api_key_secret", ""))
        if not key:
            raise ValueError("Configured AI credential unavailable")
    cache_hits, remote_calls = 0, 0

    def reviewer(payload):
        nonlocal cache_hits, remote_calls
        request = {"model": config.get("model"), "system": REVIEW_SYSTEM, "payload": payload}
        signature = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = cache_dir / f"{signature}.json"
        if path.exists():
            cache_hits += 1
            return read_json(path)["response"]
        if not args.live_ai:
            raise RuntimeError("Response is not present in the offline replay cache")
        from utils.ai_service import _chat_json
        remote_calls += 1
        response = _chat_json(config, key, REVIEW_SYSTEM, payload, 5000)
        write_json(path, {"request": request, "response": response,
                          "received_at": datetime.now().isoformat(timespec="seconds")})
        return response

    evaluation_items = sorted([s["item"] for s in snapshot["samples"]],
                              key=lambda i: hashlib.sha256(("offline-order:" + i["id"]).encode()).hexdigest())
    judgments = review_candidates(evaluation_items, snapshot["context"],
                                  reviewer=reviewer, journals=snapshot["journals"],
                                  easyscholar_ready=snapshot["easyscholar_ready"], batch_size=5,
                                  progress=lambda text, percent: print(f"{percent}% {text}", flush=True))
    lookup = {r["id"]: r for r in judgments}
    rows = [{"id": s["item"]["id"], "title": s["item"]["title"], "journal": s["item"].get("journal", ""),
             "expected_group": s["expected_group"], "reference_note": s["reference_note"],
             "old_score": s["item"].get("score"), "old_status": s["item"].get("status"),
             "old_reason": s["item"].get("recommendation_reason") or s["item"].get("ai_reason_cn", ""),
             "new": lookup[s["item"]["id"]]} for s in snapshot["samples"]]
    after = source_hashes(source)
    result = {"generated_at": datetime.now().isoformat(timespec="seconds"), "policy_version": POLICY_VERSION,
              "source_items": snapshot["total_source_items"], "context": snapshot["context"],
              "metrics": review_metrics(rows), "rows": rows, "formal_data_unchanged": before == after,
              "hashed_files": len(before), "cache_hits": cache_hits, "remote_calls": remote_calls,
              "ai_model": config.get("model"), "easyscholar_live_requests": 0}
    write_json(output / "results.json", result)
    write_json(output / "manifest.json", {"before": before, "after": after, "unchanged": before == after,
               "snapshot_source_unchanged": snapshot["source_hashes_at_snapshot"] == after,
               "mode": "live-ai" if args.live_ai else "replay", "generated_at": result["generated_at"],
               "implementation_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                         [PROJECT / "utils/frontier_admission.py", Path(__file__)]}})
    render_report(result, output)
    print(json.dumps({k: result[k] for k in ["metrics", "formal_data_unchanged", "remote_calls", "cache_hits"]}, ensure_ascii=False), flush=True)
    print(str(output / "对照报告.md"), flush=True)
    return 0 if before == after else 2


if __name__ == "__main__":
    raise SystemExit(main())
