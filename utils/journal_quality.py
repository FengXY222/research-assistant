"""Pure, source-labelled journal quality snapshots.

This module only reads already-persisted JCR and EasyScholar fields.  It never
performs network I/O and deliberately keeps AI hints out of verified metrics.
"""

from __future__ import annotations

import re
from typing import Any


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _quartile(value: Any) -> str:
    match = re.search(r"\bQ\s*([1-4])\b|([1-4])\s*区", str(value or "").upper())
    return f"Q{match.group(1) or match.group(2)}" if match else ""


def _jcr_quartile(jcr: dict[str, Any]) -> str:
    metrics = jcr.get("metrics", [])
    if not isinstance(metrics, list):
        return ""
    for metric in metrics:
        if isinstance(metric, dict):
            quartile = _quartile(metric.get("quartile", ""))
            if quartile:
                return quartile
    return ""


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def compact_cas_quartile(value: Any) -> str:
    """Extract a plain ``1区``…``4区`` label from EasyScholar text.

    EasyScholar may return strings such as ``农林科学1区`` or ``升级版 2区``.
    The library's compact row should expose the division itself, not the
    provider's subject/category prefix.
    """
    text = str(value or "").strip()
    match = re.search(r"([1-4])\s*区", text)
    return f"{match.group(1)}区" if match else ""


def journal_quality_snapshot(journal: dict[str, Any] | None) -> dict[str, Any]:
    """Return a deterministic, provenance-labelled quality view of a journal."""
    journal = _mapping(journal)
    jcr = _mapping(journal.get("jcr"))
    easyscholar = _mapping(journal.get("easyscholar"))
    jcr_status = str(jcr.get("status", "pending")).strip().casefold() or "pending"
    jcr_quartile = _jcr_quartile(jcr)
    cas_upgrade = _first_text(easyscholar.get("cas_upgrade"), easyscholar.get("cas_upgrade_small"))
    cas_basic = _first_text(easyscholar.get("cas_basic"))
    impact_factor = _first_text(easyscholar.get("impact_factor"), easyscholar.get("if"))
    parts: list[str] = []
    if jcr_quartile:
        parts.append(f"JCR {jcr_quartile}")
    if cas_upgrade:
        parts.append(f"中科院升级版 {cas_upgrade}")
    if cas_basic:
        parts.append(f"中科院基础版 {cas_basic}")
    if impact_factor:
        parts.append(f"IF {impact_factor}")
    jcr_source = str(jcr.get("source", "")).strip()
    easy_source = str(easyscholar.get("source", "")).strip()
    source = _first_text(jcr_source, easy_source)
    checked_at = _first_text(jcr.get("checked_at"), easyscholar.get("checked_at"))
    is_verified = jcr_status in {"verified", "manual"} and bool(jcr_quartile)
    return {
        "jcr_status": jcr_status,
        "jcr_quartile": jcr_quartile,
        "cas_upgrade": cas_upgrade,
        "cas_basic": cas_basic,
        "metric_line": " · ".join(parts),
        "source": source,
        "checked_at": checked_at,
        "is_verified": is_verified,
    }


def compact_metric_line(journal: dict[str, Any] | None) -> str:
    """Return the short metric line used in narrow journal-facing rows."""
    snapshot = journal_quality_snapshot(journal)
    parts: list[str] = []
    quartile = str(snapshot.get("jcr_quartile", "")).strip()
    if quartile:
        parts.append(f"JCR {quartile}")
    cas = compact_cas_quartile(snapshot.get("cas_upgrade", "") or snapshot.get("cas_basic", ""))
    if cas:
        parts.append(f"中科院 {cas}")
    impact_factor = str(journal_quality_snapshot(journal).get("metric_line", ""))
    if "IF " in impact_factor:
        value = impact_factor.split("IF ", 1)[1].split(" · ", 1)[0].strip()
        if value:
            parts.append(f"IF {value}")
    return " · ".join(parts)


def is_default_frontier_eligible(
    snapshot: dict[str, Any] | None,
    *,
    easyscholar_configured: bool = False,
) -> tuple[bool, str]:
    """Apply the default quality gate without inventing missing provider data."""
    snapshot = _mapping(snapshot)
    quartile = str(snapshot.get("jcr_quartile", "")).upper()
    status = str(snapshot.get("jcr_status", "pending")).casefold()
    if status in {"verified", "manual"} and quartile in {"Q3", "Q4"}:
        return False, "known_q3_q4"
    if status in {"verified", "manual"} and quartile in {"Q1", "Q2"}:
        return True, "verified_q1_q2"
    if easyscholar_configured and not bool(snapshot.get("is_verified")):
        return False, "quality_pending"
    return True, "quality_unresolved"
