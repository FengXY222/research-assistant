"""Global/per-paper profile and full-scope AI matching contracts."""

from __future__ import annotations

from unittest.mock import patch

from utils.special_issue_matching import build_special_issue_profiles, match_special_issue


def _profiles() -> dict:
    return build_special_issue_profiles(
        {
            "terms": [
                {"canonical_en": "soil organic carbon", "translation_zh": "土壤有机碳", "weight": 95, "locked": True},
                {"canonical_en": "digital soil mapping", "translation_zh": "数字土壤制图", "weight": 82},
            ],
            "excluded_entries": [
                {"canonical_en": "soil microbial community", "translation_zh": "土壤微生物群落"}
            ],
            "blocked_terms": [{"canonical_en": "metagenomics"}],
        },
        [
            {
                "id": "p1",
                "title": "SOC mapping",
                "keywords": ["soil organic carbon", "remote sensing"],
                "summary": "Maps soil organic carbon using satellite imagery and machine learning.",
            },
            {
                "id": "p2",
                "title": "Heavy metal mapping",
                "keywords": ["soil heavy metals", "digital soil mapping"],
                "summary": "Spatial prediction of soil heavy metals.",
            },
        ],
    )


def _issue(**overrides) -> dict:
    return {
        "id": "si-1",
        "title": "Remote Sensing for Soil Carbon and Contaminant Mapping",
        "type": "special_issue",
        "journal": "Geoderma",
        "deadline": "2027-06-30",
        "scope_text": "We invite full papers on remote sensing, digital soil mapping, soil carbon and contaminant prediction.",
        "scope_is_complete": True,
        "scope_status": "full",
        **overrides,
    }


def _assessment(score: int, *, paper_id: str | None = None, refs: list[str]) -> dict:
    result = {
        "score": score,
        "reason": "征稿完整范围覆盖论文的研究问题。",
        "risk": "仍需在投稿前核对具体方法边界。",
        "branch": "scope:soil_carbon_protection",
        "relation": "core",
        "evidence_refs": refs,
        "exclusion_assessment": {
            "status": "none",
            "reason": "未触发排除方向。",
            "evidence_refs": [],
        },
    }
    if paper_id is not None:
        result["paper_id"] = paper_id
    return result


def _response(item: dict, score: int = 88) -> dict:
    refs = [row["id"] for row in item["scope_paragraphs"]]
    return {
        **_assessment(score, refs=[refs[0]]),
        "matched_terms": ["soil organic carbon", "digital soil mapping"],
        "scope_coverage": refs,
        "paper_matches": [
            _assessment(91, paper_id="p1", refs=[refs[0]]),
            _assessment(79, paper_id="p2", refs=[refs[0]]),
        ],
        "model": "fixture",
    }


def test_profiles_keep_global_long_term_signals_and_independent_paper_scopes() -> None:
    profiles = _profiles()

    assert profiles["global"]["active_terms"][0]["locked"] is True
    assert {paper["id"] for paper in profiles["global"]["papers"]} == {"p1", "p2"}
    assert profiles["papers"]["p1"]["title"] == "SOC mapping"
    assert profiles["papers"]["p2"]["keywords"] == ["soil heavy metals", "digital soil mapping"]
    assert "soil microbial community" in profiles["global"]["excluded_terms"]
    assert "metagenomics" not in profiles["papers"]["p1"]["excluded_terms"]


def test_one_issue_returns_one_card_with_all_known_matching_papers() -> None:
    def matcher(_item, _profiles, _progress=None):
        return _response(_item)

    result = match_special_issue(_issue(), _profiles(), ai_matcher=matcher)

    assert result["formal"] is True
    assert result["score"] == 88
    assert [paper["paper_id"] for paper in result["matched_papers"]] == ["p1", "p2"]
    assert result["profile_scope"] == "global_and_papers"


def test_ai_contextual_exclusion_can_remove_formal_status_without_changing_score() -> None:
    def matcher(item, *_args):
        raw = _response(item, 92)
        raw.update(
            branch="unrelated",
            relation="unrelated",
            exclusion_assessment={
                "status": "primary",
                "reason": "征稿仅接受被排除的独立研究方向。",
                "evidence_refs": raw["evidence_refs"],
            },
        )
        return raw

    result = match_special_issue(
        _issue(scope_text="Soil microbial community and metagenomics are the exclusive focus."),
        _profiles(),
        ai_matcher=matcher,
    )

    assert result["score"] == 92
    assert result["formal"] is False
    assert result["status"] == "out_of_scope"


def test_missing_full_scope_waits_instead_of_fabricating_a_score() -> None:
    called = []
    result = match_special_issue(
        _issue(scope_text=""),
        _profiles(),
        ai_matcher=lambda *_args: called.append(True),
    )

    assert called == []
    assert result["status"] == "awaiting_scope"
    assert result["formal"] is False


def test_ai_contract_sends_complete_scope_and_keeps_independent_paper_scores() -> None:
    from utils import ai_service

    issue = _issue(scope_text="FULL SCOPE " + "soil carbon and remote sensing. " * 80)
    profiles = _profiles()
    captured = {}

    def fake_chat(_config, _key, _system, payload, _max_tokens):
        captured.update(payload)
        return _response(payload["special_issue"], 84)

    with (
        patch.object(ai_service, "get_ai_settings", return_value={"enabled": True, "model": "deepseek-chat", "api_key_secret": "encrypted"}),
        patch.object(ai_service, "load_app_settings", return_value={"ai": {}}),
        patch.object(ai_service, "reveal_secret", return_value="secret"),
        patch.object(ai_service, "_chat_json", side_effect=fake_chat),
    ):
        result = ai_service.score_special_issue_with_ai(issue, profiles)

    supplied_scope = " ".join(row["text"] for row in captured["special_issue"]["scope_paragraphs"])
    assert "".join(supplied_scope.split()) == "".join(issue["scope_text"].split())
    assert [row["paper_id"] for row in result["paper_matches"] if row["formal"]] == ["p1", "p2"]
    assert result["model"] == "deepseek-chat"
