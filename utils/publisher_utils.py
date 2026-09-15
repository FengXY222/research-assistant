"""Canonical publisher names and non-destructive fuzzy validation helpers."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


_LEGAL_TOKENS = {
    "bv",
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "llc",
    "ltd",
    "plc",
    "publishing",
    "publisher",
    "publications",
    "press",
    "the",
}


def canonical_publisher(value: str) -> str:
    """Collapse publisher imprints and legal entities into their parent brand."""
    publisher = str(value or "").strip()
    key = publisher.casefold()
    if not key:
        return ""
    if any(value in key for value in ("elsevier", "academic press", "pergamon", "cell press", "woodhead", "churchill livingstone")):
        return "Elsevier"
    if any(value in key for value in ("springer", "nature portfolio", "biomed central", "palgrave")) or key == "bmc":
        return "Springer Nature"
    if "wiley" in key or "blackwell" in key or "hindawi" in key:
        return "Wiley"
    if "taylor" in key or "informa" in key or "routledge" in key or "crc press" in key:
        return "Taylor & Francis"
    if "mdpi" in key:
        return "MDPI"
    if "copernicus" in key:
        return "Copernicus Publications"
    if "csiro" in key:
        return "CSIRO Publishing"
    if "sage" in key:
        return "SAGE"
    if "frontiers" in key:
        return "Frontiers"
    if "american chemical society" in key or key == "acs":
        return "ACS"
    if "oxford university press" in key or key == "oup":
        return "Oxford University Press"
    if "cambridge university press" in key:
        return "Cambridge University Press"
    return publisher


def _publisher_key(value: str) -> str:
    """Return a punctuation- and legal-suffix-insensitive comparison key."""
    text = str(value or "").casefold()
    text = re.sub(r"[^\w\s&]+", " ", text, flags=re.UNICODE)
    tokens = [token for token in text.split() if token not in _LEGAL_TOKENS]
    return " ".join(tokens)


def fuzzy_publisher_check(recorded: str, observed: str) -> dict[str, str]:
    """Compare a stored publisher with an external observation without filtering.

    The function deliberately returns ``unknown`` when either side is absent.
    ``probable`` is a review hint, while only ``match`` means the two values
    are confidently equivalent.  Callers must never use ``mismatch`` as a
    deletion gate; it is only a visible warning for manual review.
    """
    recorded_text = str(recorded or "").strip()
    observed_text = str(observed or "").strip()
    if not recorded_text or not observed_text:
        return {
            "status": "unknown",
            "canonical": canonical_publisher(observed_text or recorded_text),
            "recorded": recorded_text,
            "observed": observed_text,
            "reason": "缺少已填写或外部来源的出版社信息，暂不校验。",
        }

    recorded_canonical = canonical_publisher(recorded_text)
    observed_canonical = canonical_publisher(observed_text)
    recorded_key = _publisher_key(recorded_canonical)
    observed_key = _publisher_key(observed_canonical)
    if recorded_key and recorded_key == observed_key:
        status = "match"
        reason = "出版社品牌及法定实体后缀相符。"
    elif recorded_key and observed_key and (
        recorded_key in observed_key
        or observed_key in recorded_key
        or SequenceMatcher(None, recorded_key, observed_key).ratio() >= 0.78
        or bool(set(recorded_key.split()) & set(observed_key.split()))
    ):
        status = "probable"
        reason = "出版社名称存在品牌或关键词重合，建议投稿前人工确认。"
    else:
        status = "mismatch"
        reason = "已填写出版社与外部来源不一致，请核对期刊官网。"
    return {
        "status": status,
        "canonical": observed_canonical or recorded_canonical,
        "recorded": recorded_text,
        "observed": observed_text,
        "reason": reason,
    }
