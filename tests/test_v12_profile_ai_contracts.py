"""Structured AI contract for safe daily research-profile organization."""

from __future__ import annotations

from utils import ai_service
from utils.research_profile_service import normalize_research_profile_v12, remove_term


def test_daily_organizer_sends_locks_blocks_and_returns_a_change_plan(monkeypatch) -> None:
    profile = normalize_research_profile_v12(
        {
            "terms": [
                {"canonical_en": "soil carbon", "translation_zh": "土壤碳", "locked": True},
                {"canonical_en": "remote sensing", "translation_zh": "遥感", "weight": 60},
            ]
        },
        today="2026-08-31",
    )
    profile = remove_term(profile, profile["terms"][1]["id"], today="2026-08-31")
    captured = {}
    progress = []

    monkeypatch.setattr(
        ai_service,
        "_require_config",
        lambda feature: ({"model": "test-model", "research_profile_update": True}, "secret"),
    )

    def fake_chat(config, key, system, payload, max_tokens):
        captured.update({"system": system, "payload": payload, "max_tokens": max_tokens})
        return {
            "terms": [
                {
                    "canonical_en": "soil organic carbon",
                    "translation_zh": "土壤有机碳",
                    "weight": 82,
                    "evidence": ["existing paper"],
                    "confidence": "high",
                }
            ],
            "excluded_terms": [
                {"canonical_en": "soil microbes", "translation_zh": "土壤微生物", "reason": "negative feedback"}
            ],
            "delete_term_ids": [],
            "delete_excluded_term_ids": [],
            "merge_terms": [],
        }

    monkeypatch.setattr(ai_service, "_chat_json", fake_chat)

    proposal = ai_service.organize_research_profile_with_ai(
        profile,
        {"papers": [{"id": "p1", "title": "SOC mapping"}], "signals": []},
        progress=lambda message, value=None: progress.append((message, value)),
    )

    sent_profile = captured["payload"]["current_profile"]
    assert sent_profile["terms"][0]["locked"] is True
    assert sent_profile["blocked_terms"][0]["canonical_key"] == "remote sensing"
    assert proposal["terms"][0]["translation_zh"] == "土壤有机碳"
    assert proposal["excluded_terms"][0]["translation_zh"] == "土壤微生物"
    assert progress[0][1] == 10
    assert progress[-1][1] == 100


def test_daily_organizer_rejects_an_ai_locked_term(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_service,
        "_require_config",
        lambda feature: ({"model": "test-model", "research_profile_update": True}, "secret"),
    )
    monkeypatch.setattr(
        ai_service,
        "_chat_json",
        lambda *args: {"terms": [{"canonical_en": "invalid", "weight": 100, "locked": True}]},
    )

    try:
        ai_service.organize_research_profile_with_ai({}, {})
    except ValueError as error:
        assert "锁定" in str(error)
    else:
        raise AssertionError("AI-created locks must be rejected")


def test_pdf_keyword_extraction_returns_bilingual_weighted_terms(monkeypatch) -> None:
    progress = []
    monkeypatch.setattr(
        ai_service,
        "_require_config",
        lambda feature: ({"model": "test-model", "research_profile_update": True}, "secret"),
    )
    monkeypatch.setattr(
        ai_service,
        "_chat_json",
        lambda *args: {
            "terms": [
                {
                    "canonical_en": "soil organic carbon",
                    "translation_zh": "土壤有机碳",
                    "weight": 91,
                    "confidence": "high",
                    "category": "research_object",
                    "evidence": ["full text"],
                },
                {
                    "canonical_en": "remote sensing",
                    "translation_zh": "遥感",
                    "weight": 72,
                    "confidence": "medium",
                    "category": "method",
                    "evidence": ["abstract"],
                },
            ],
            "reason_cn": "来自研究对象和方法",
        },
    )

    result = ai_service.extract_research_keywords_with_ai(
        {"source_type": "pdf", "summary": "full OCR text"},
        progress=lambda message, value=None: progress.append((message, value)),
    )

    assert result["keywords"] == ["soil organic carbon", "remote sensing"]
    assert result["terms"][0]["translation_zh"] == "土壤有机碳"
    assert result["terms"][0]["weight"] == 91
    assert result["terms"][0]["source"] == "ai_pdf"
    assert progress[-1][1] == 100


def test_keyword_extraction_keeps_legacy_keyword_response_compatible(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_service,
        "_require_config",
        lambda feature: ({"model": "test-model", "research_profile_update": True}, "secret"),
    )
    monkeypatch.setattr(ai_service, "_chat_json", lambda *args: {"keywords": ["soil carbon"]})

    result = ai_service.extract_research_keywords_with_ai({"source_type": "paper", "summary": "soil carbon"})

    assert result["keywords"] == ["soil carbon"]
    assert result["terms"][0]["canonical_en"] == "soil carbon"
    assert result["terms"][0]["translation_zh"] == ""


def test_ocr_pages_are_cleaned_before_keyword_extraction(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        ai_service,
        "_require_config",
        lambda feature: ({"model": "test-model", "research_profile_update": True}, "secret"),
    )

    def fake_chat(config, key, system, payload, max_tokens):
        calls.append(payload)
        if payload["task"].startswith("清理扫描 PDF"):
            return {
                "pages": [
                    {
                        "page_number": 2,
                        "cleaned_text": "soil heavy metal mapping with remote sensing",
                    }
                ]
            }
        return {
            "terms": [
                {
                    "canonical_en": "soil heavy metal mapping",
                    "translation_zh": "土壤重金属制图",
                    "weight": 86,
                    "confidence": "high",
                    "evidence": ["cleaned OCR text"],
                }
            ]
        }

    monkeypatch.setattr(ai_service, "_chat_json", fake_chat)

    result = ai_service.extract_research_keywords_with_ai(
        {
            "source_type": "pdf",
            "pages": [
                {"page_number": 1, "source": "native", "text": "Native abstract"},
                {"page_number": 2, "source": "ocr", "text": "S0IL HEAVY METAL MAPP1NG page 2"},
            ],
            "summary": "unclean fallback",
        }
    )

    assert len(calls) == 2
    assert "soil heavy metal mapping with remote sensing" in calls[1]["text"]
    assert result["terms"][0]["translation_zh"] == "土壤重金属制图"


def test_one_line_comment_classifier_returns_a_bilingual_change_plan(monkeypatch) -> None:
    captured = {}
    progress = []
    monkeypatch.setattr(
        ai_service,
        "_require_config",
        lambda feature: ({"model": "test-model", "research_profile_update": True}, "secret"),
    )

    def fake_chat(config, key, system, payload, max_tokens):
        captured.update({"system": system, "payload": payload})
        return {
            "intent": "negative",
            "active_terms": [],
            "excluded_terms": [
                {"canonical_en": "soil microorganisms", "translation_zh": "土壤微生物"}
            ],
            "pending_terms": [],
            "reason": "用户明确排除该方向",
            "confidence": "high",
        }

    monkeypatch.setattr(ai_service, "_chat_json", fake_chat)

    result = ai_service.classify_profile_comment_with_ai(
        "涉及土壤微生物，不是我的研究方向",
        {"id": "work-1", "title": "Microbial carbon", "matched_terms": ["soil carbon"]},
        progress=lambda message, value=None: progress.append((message, value)),
    )

    assert captured["payload"]["text"] == "涉及土壤微生物，不是我的研究方向"
    assert result["intent"] == "negative"
    assert result["excluded_terms"][0]["translation_zh"] == "土壤微生物"
    assert result["item_id"] == "work-1"
    assert progress[0][1] == 10
    assert progress[-1][1] == 100
