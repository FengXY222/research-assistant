"""V13 source catalogue, encrypted credentials and independent health state."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Callable

from utils.secure_store import SecretStoreError, protect_secret, reveal_secret


SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "openalex": {"name": "OpenAlex", "group": "frontier_core", "key_mode": "optional", "default_enabled": True},
    "crossref": {"name": "Crossref", "group": "frontier_core", "key_mode": "none", "contact_email": True, "default_enabled": True},
    "semantic_scholar": {"name": "Semantic Scholar", "group": "frontier_core", "key_mode": "recommended", "default_enabled": True},
    "europe_pmc": {"name": "Europe PMC", "group": "frontier_core", "key_mode": "none", "default_enabled": True},
    "doaj": {"name": "DOAJ", "group": "frontier_core", "key_mode": "optional", "default_enabled": True},
    "arxiv": {"name": "arXiv", "group": "frontier_core", "key_mode": "none", "default_enabled": True},
    "ncbi": {"name": "NCBI / PubMed", "group": "frontier_fallback", "key_mode": "optional", "default_enabled": True},
    "core": {"name": "CORE", "group": "frontier_optional", "key_mode": "required", "default_enabled": False},
    "elsevier": {"name": "Elsevier 官网（停用，改由第三方发现）", "group": "special_official", "key_mode": "none", "default_enabled": False, "retired": True},
    "springer_nature": {"name": "Springer Nature", "group": "special_official", "key_mode": "optional", "default_enabled": True},
    "wiley": {"name": "Wiley", "group": "special_official", "key_mode": "optional", "default_enabled": True},
    "taylor_francis": {"name": "Taylor & Francis", "group": "special_official", "key_mode": "optional", "default_enabled": True},
    "mdpi": {"name": "MDPI", "group": "special_official", "key_mode": "none", "default_enabled": True},
    "frontiers": {"name": "Frontiers", "group": "special_official", "key_mode": "none", "default_enabled": True},
    "geodeadlines": {"name": "GeoDeadlines", "group": "special_third_party", "key_mode": "none", "default_enabled": True},
    "research_collection_radar": {"name": "research-collection-radar", "group": "special_third_party", "key_mode": "none", "default_enabled": True},
    "journal_cfp_ddl": {"name": "journal-cfp-ddl", "group": "special_third_party", "key_mode": "none", "default_enabled": True},
}

RECALL_STRATEGIES = (
    "keyword",
    "semantic_related",
    "citation_network",
    "confirmed_author_team",
    "watched_journal",
    "topic_expansion",
)
RECALL_OUTCOMES = {"NO_SEED", "FAILED", "SUCCESS_EMPTY", "SUCCESS"}
HEALTH_STATES = {"HEALTHY", "DEGRADED", "UNAVAILABLE"}


def default_source_settings() -> dict[str, dict[str, Any]]:
    return {
        source_id: {
            "enabled": bool(spec.get("default_enabled", False)),
            "api_key_secret": "",
            "secret_last4": "",
            "contact_email": "" if spec.get("contact_email") else None,
            "validation_status": "not_required" if spec["key_mode"] == "none" else "not_configured",
            "validated_at": "",
        }
        for source_id, spec in SOURCE_REGISTRY.items()
    }


def normalize_source_settings(raw: Any) -> dict[str, dict[str, Any]]:
    raw = raw if isinstance(raw, dict) else {}
    result = default_source_settings()
    for source_id, defaults in result.items():
        value = raw.get(source_id, {})
        value = value if isinstance(value, dict) else {}
        spec = SOURCE_REGISTRY[source_id]
        status = str(value.get("validation_status", defaults["validation_status"])).strip().casefold()
        if status not in {
            "not_required", "not_configured", "valid", "authentication_failed",
            "network_failed", "rate_limited", "service_unavailable", "not_checked",
        }:
            status = "not_checked"
        result[source_id] = {
            "enabled": False if spec.get("retired") else bool(value.get("enabled", defaults["enabled"])),
            "api_key_secret": str(value.get("api_key_secret", "")).strip(),
            "secret_last4": str(value.get("secret_last4", "")).strip()[-4:],
            "contact_email": str(value.get("contact_email", "")).strip()[:254] if spec.get("contact_email") else None,
            "validation_status": status,
            "validated_at": str(value.get("validated_at", "")).strip()[:40],
        }
    return result


def update_source_secret(
    settings: dict[str, dict[str, Any]],
    source_id: str,
    secret: str,
    *,
    protector: Callable[[str], str] = protect_secret,
) -> dict[str, dict[str, Any]]:
    if source_id not in SOURCE_REGISTRY:
        raise KeyError(source_id)
    result = normalize_source_settings(settings)
    value = str(secret or "").strip()
    if value:
        result[source_id]["api_key_secret"] = protector(value)
        result[source_id]["secret_last4"] = value[-4:]
        result[source_id]["validation_status"] = "not_checked"
    else:
        result[source_id]["api_key_secret"] = ""
        result[source_id]["secret_last4"] = ""
        result[source_id]["validation_status"] = (
            "not_required" if SOURCE_REGISTRY[source_id]["key_mode"] == "none" else "not_configured"
        )
    result[source_id]["validated_at"] = ""
    return result


def runtime_source_config(
    settings: Any,
    source_id: str,
    *,
    revealer: Callable[[str], str] = reveal_secret,
) -> dict[str, Any]:
    normalized = normalize_source_settings(settings)
    if source_id not in normalized:
        raise KeyError(source_id)
    value = normalized[source_id]
    secret = ""
    token = str(value.get("api_key_secret", ""))
    if token:
        try:
            secret = revealer(token)
        except SecretStoreError:
            secret = ""
    return {
        "id": source_id,
        "name": SOURCE_REGISTRY[source_id]["name"],
        "enabled": bool(value["enabled"]),
        "api_key": secret,
        "contact_email": value.get("contact_email") or "",
        "key_mode": SOURCE_REGISTRY[source_id]["key_mode"],
    }


def migrate_legacy_frontier_secrets(
    profile: Any,
    source_settings: Any,
    *,
    protector: Callable[[str], str] = protect_secret,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    """Move legacy plaintext profile keys only after encryption succeeds."""

    result_profile = deepcopy(profile if isinstance(profile, dict) else {})
    result_settings = normalize_source_settings(source_settings)
    migrated: list[str] = []
    sources = result_profile.get("sources", {})
    sources = deepcopy(sources if isinstance(sources, dict) else {})
    for source_id, value in sources.items():
        if source_id not in result_settings or not isinstance(value, dict):
            continue
        plaintext = str(value.get("api_key", "")).strip()
        if plaintext and not result_settings[source_id]["api_key_secret"]:
            result_settings = update_source_secret(result_settings, source_id, plaintext, protector=protector)
            migrated.append(source_id)
        clean = {key: deepcopy(entry) for key, entry in value.items() if key != "api_key"}
        clean["enabled"] = bool(value.get("enabled", result_settings[source_id]["enabled"]))
        clean["credential_ref"] = f"settings.data_sources.{source_id}"
        sources[source_id] = clean
    result_profile["sources"] = sources
    return result_profile, result_settings, migrated


def masked_source_summary(settings: Any, source_id: str) -> str:
    values = normalize_source_settings(settings)[source_id]
    spec = SOURCE_REGISTRY[source_id]
    if spec["key_mode"] == "none":
        return "无需 Key"
    suffix = str(values.get("secret_last4", ""))
    return f"已配置 · …{suffix}" if values.get("api_key_secret") else "未配置"


def normalize_health(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    state = str(value.get("state", "HEALTHY")).strip().upper()
    if state not in HEALTH_STATES:
        state = "HEALTHY"
    return {
        "state": state,
        "consecutive_failures": max(0, int(value.get("consecutive_failures", 0) or 0)),
        "last_attempt_at": str(value.get("last_attempt_at", "")),
        "last_success_at": str(value.get("last_success_at", "")),
        "last_result_count": max(0, int(value.get("last_result_count", 0) or 0)),
        "last_error_type": str(value.get("last_error_type", ""))[:80],
        "last_error": str(value.get("last_error", ""))[:240],
        "next_probe_at": str(value.get("next_probe_at", "")),
        "successful_watermark": str(value.get("successful_watermark", "")),
    }


def record_source_outcome(
    previous: Any,
    *,
    at: datetime,
    success: bool,
    result_count: int = 0,
    error_type: str = "",
    error: str = "",
    successful_watermark: str = "",
) -> dict[str, Any]:
    """Update one source independently; failures never advance its watermark."""

    result = normalize_health(previous)
    result["last_attempt_at"] = at.isoformat(timespec="seconds")
    if success:
        result.update(
            {
                "state": "HEALTHY",
                "consecutive_failures": 0,
                "last_success_at": at.isoformat(timespec="seconds"),
                "last_result_count": max(0, int(result_count)),
                "last_error_type": "",
                "last_error": "",
                "next_probe_at": "",
            }
        )
        if successful_watermark:
            result["successful_watermark"] = str(successful_watermark)
        return result
    failures = result["consecutive_failures"] + 1
    result.update(
        {
            "consecutive_failures": failures,
            "state": "UNAVAILABLE" if failures >= 3 else "DEGRADED",
            "last_error_type": str(error_type or "unknown")[:80],
            "last_error": str(error or "来源暂不可用")[:240],
        }
    )
    if failures >= 3:
        result["next_probe_at"] = (at + timedelta(hours=24)).isoformat(timespec="seconds")
    return result


def source_due(health: Any, *, at: datetime, manual: bool = False) -> bool:
    if manual:
        return True
    value = normalize_health(health)
    if value["state"] != "UNAVAILABLE" or not value["next_probe_at"]:
        return True
    try:
        probe = datetime.fromisoformat(value["next_probe_at"])
    except ValueError:
        return True
    if probe.tzinfo is not None and at.tzinfo is None:
        probe = probe.replace(tzinfo=None)
    return at >= probe


def recall_outcome(*, has_seed: bool, succeeded: bool, count: int = 0, error: str = "") -> dict[str, Any]:
    if not has_seed:
        status = "NO_SEED"
    elif not succeeded:
        status = "FAILED"
    elif count <= 0:
        status = "SUCCESS_EMPTY"
    else:
        status = "SUCCESS"
    return {"status": status, "count": max(0, int(count)), "error": str(error)[:240] if status == "FAILED" else ""}
