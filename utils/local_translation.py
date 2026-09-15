"""Lazy local Chinese translation helpers with an open-source backend hook."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


Translator = Callable[[list[str]], list[str]]


def _translate(texts: list[str], translator: Translator | None) -> list[str]:
    if not texts:
        return []
    if translator is None:
        from utils.local_translation_backend import translate_en_to_zh

        translator = translate_en_to_zh
    values = translator(texts)
    if not isinstance(values, list) or len(values) != len(texts):
        raise RuntimeError("本地翻译工具返回数量不一致")
    return [str(value).strip() for value in values]


def translate_missing_keywords(terms: list[dict[str, Any]], *, translator: Translator | None = None) -> list[dict[str, Any]]:
    result = [deepcopy(value) for value in terms if isinstance(value, dict)]
    positions = [
        index
        for index, value in enumerate(result)
        if str(value.get("canonical_en", value.get("text", ""))).strip()
        and not str(value.get("translation_zh", "")).strip()
    ]
    source = [str(result[index].get("canonical_en", result[index].get("text", ""))).strip() for index in positions]
    for index, translation in zip(positions, _translate(source, translator), strict=True):
        result[index]["translation_zh"] = translation
    return result


def translate_profile_keywords(profile: dict[str, Any], *, translator: Translator | None = None) -> dict[str, Any]:
    result = deepcopy(profile if isinstance(profile, dict) else {})
    for field in ("terms", "pending_terms", "excluded_entries"):
        values = result.get(field, [])
        if isinstance(values, list):
            result[field] = translate_missing_keywords(values, translator=translator)
    return result


def translate_special_issue_scope(scope: str, existing: str = "", *, translator: Translator | None = None) -> str:
    if str(existing or "").strip():
        return str(existing).strip()
    text = str(scope or "").strip()
    if not text:
        return ""
    return _translate([text], translator)[0]
