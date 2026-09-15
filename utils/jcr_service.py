"""Optional, verifiable Journal Citation Reports metadata integration.

Clarivate's Journals API is entitlement-protected.  The application therefore
uses a user supplied endpoint template and marks results as verified only when
the configured API actually returns a Q1–Q4 value.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from utils.file_manager import load_app_settings
from utils.secure_store import SecretStoreError, reveal_secret


class JcrConfigurationError(RuntimeError):
    """Raised when Clarivate configuration is missing or inaccessible."""


class JcrRequestError(RuntimeError):
    """A safe, UI-ready Clarivate Journals API error."""


def get_jcr_settings() -> dict[str, Any]:
    raw = load_app_settings().get("jcr", {})
    raw = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "endpoint_template": str(raw.get("endpoint_template", "")).strip(),
        "api_key_secret": str(raw.get("api_key_secret", "")).strip(),
    }


def is_jcr_ready() -> bool:
    config = get_jcr_settings()
    return bool(config["enabled"] and config["endpoint_template"] and config["api_key_secret"])


def _require_config() -> tuple[dict[str, Any], str]:
    config = get_jcr_settings()
    if not config["enabled"]:
        raise JcrConfigurationError("请先在“设置 → 智能增强与 JCR”启用 Clarivate JCR 核验。")
    if not config["endpoint_template"]:
        raise JcrConfigurationError("请在设置中粘贴 Clarivate Journals API 的请求地址模板。")
    try:
        api_key = reveal_secret(config["api_key_secret"])
    except SecretStoreError as error:
        raise JcrConfigurationError(str(error)) from error
    if not api_key:
        raise JcrConfigurationError("请先在设置中填写 Clarivate Journals API Key。")
    return config, api_key


def _endpoint(template: str, journal: dict[str, Any]) -> str:
    issn = str(journal.get("issn", "")).strip()
    name = str(journal.get("name", "")).strip()
    if not issn and not name:
        raise JcrRequestError("该期刊缺少刊名和 ISSN，无法进行 JCR 核验。")
    values = {"issn": quote(issn, safe=""), "name": quote(name, safe="")}
    if "{" in template:
        try:
            return template.format(**values)
        except (KeyError, ValueError) as error:
            raise JcrConfigurationError("JCR 地址模板仅支持 {issn} 和 {name} 占位符。") from error
    separator = "&" if "?" in template else "?"
    key, value = ("issn", values["issn"]) if issn else ("name", values["name"])
    return f"{template}{separator}{key}={value}"


def _first(mapping: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    folded = {str(key).casefold(): value for key, value in mapping.items()}
    for alias in aliases:
        if alias.casefold() in folded:
            return folded[alias.casefold()]
    return ""


def _quartile(value: Any) -> str:
    match = re.search(r"\bQ([1-4])\b", str(value or "").upper())
    return f"Q{match.group(1)}" if match else ""


def _metrics_from_payload(payload: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            quartile = _quartile(
                _first(value, ("quartile", "jcrQuartile", "jifQuartile", "jcr_quartile", "jif_quartile"))
            )
            if quartile:
                year_raw = _first(value, ("year", "jcrYear", "jifYear", "editionYear"))
                try:
                    year = int(year_raw)
                except (TypeError, ValueError):
                    year = 0
                category = _first(
                    value,
                    ("category", "categoryName", "subjectCategory", "subject_category", "webOfScienceCategory"),
                )
                if isinstance(category, list):
                    category = " / ".join(str(item) for item in category if str(item).strip())
                jif = _first(value, ("jif", "journalImpactFactor", "impactFactor", "journal_impact_factor"))
                rank = _first(value, ("rank", "jifRank", "categoryRank"))
                total = _first(value, ("total", "totalJournals", "categoryTotal"))
                candidates.append(
                    {
                        "quartile": quartile,
                        "year": year,
                        "category": " ".join(str(category or "").split())[:180],
                        "jif": " ".join(str(jif or "").split())[:30],
                        "rank": " ".join(str(rank or "").split())[:24],
                        "total": " ".join(str(total or "").split())[:24],
                    }
                )
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for item in candidates:
        key = (item["quartile"], int(item["year"]), item["category"].casefold())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:12]


def _request_jcr(journal: dict[str, Any], config: dict[str, Any], api_key: str) -> dict[str, Any]:
    request = Request(
        _endpoint(config["endpoint_template"], journal),
        headers={
            "X-ApiKey": api_key,
            "Accept": "application/json",
            "User-Agent": "ScientificAssistant/0.8",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=35) as response:  # noqa: S310 - user pasted the entitlement endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise JcrRequestError(f"Clarivate JCR 请求失败（HTTP {error.code}）。") from error
    except (URLError, TimeoutError) as error:
        raise JcrRequestError("无法连接 Clarivate JCR API，请检查网络、地址模板或授权。") from error
    except json.JSONDecodeError as error:
        raise JcrRequestError("Clarivate JCR API 未返回 JSON 数据，请检查地址模板。") from error
    return {
        "status": "verified" if _metrics_from_payload(payload) else "not_found",
        "source": "Clarivate Journals API",
        "checked_at": date.today().isoformat(),
        "metrics": _metrics_from_payload(payload),
    }


def verify_journal_library(journals: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify a compact batch at Clarivate's documented five requests/second cap."""
    config, api_key = _require_config()
    updated = [dict(journal) for journal in journals]
    changed = 0
    errors: list[str] = []
    for index, journal in enumerate(updated):
        if index:
            time.sleep(0.22)
        name = str(journal.get("name", "未命名期刊"))
        try:
            jcr = _request_jcr(journal, config, api_key)
        except (JcrRequestError, JcrConfigurationError) as error:
            errors.append(f"{name}：{error}")
            continue
        if journal.get("jcr") != jcr:
            journal["jcr"] = jcr
            changed += 1
    return {"journals": updated, "changed": changed, "errors": errors}


def primary_jcr_quartile(journal: dict[str, Any]) -> str:
    raw = journal.get("jcr", {})
    raw = raw if isinstance(raw, dict) else {}
    metrics = raw.get("metrics", [])
    values = [_quartile(item.get("quartile", "")) for item in metrics if isinstance(item, dict)] if isinstance(metrics, list) else []
    values = [value for value in values if value]
    return sorted(values)[0] if values else ""


def jcr_label(journal: dict[str, Any]) -> str:
    raw = journal.get("jcr", {})
    raw = raw if isinstance(raw, dict) else {}
    quartiles = []
    metrics = raw.get("metrics", [])
    for item in metrics if isinstance(metrics, list) else []:
        if isinstance(item, dict):
            value = _quartile(item.get("quartile", ""))
            if value and value not in quartiles:
                quartiles.append(value)
    if quartiles:
        year = next((str(item.get("year")) for item in metrics if isinstance(item, dict) and item.get("year")), "")
        source = str(raw.get("source", "")).casefold()
        if raw.get("status") == "ai_estimated":
            prefix = "AI 估计"
        elif "easyscholar" in source:
            prefix = "EasyScholar"
        else:
            prefix = "JCR"
        return f"{prefix} {year + ' ' if year else ''}{'/'.join(sorted(quartiles))}".strip()
    if raw.get("status") == "not_found":
        return "JCR 未收录"
    return "JCR 待核"
