"""Optional, cached EasyScholar journal metrics with safe local precedence.

The public endpoint requires a user-owned secret key.  This module never
prints that key or the generated request URL and can be fully exercised with a
mock request function in tests.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import re
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from utils.api_rate_limit import rate_limited_urlopen as urlopen

from utils.file_manager import load_app_settings
from utils.secure_store import SecretStoreError, reveal_secret


EASYSCHOLAR_OPEN_ENDPOINT = "https://easyscholar.cc/open/getPublicationRank"
EASYSCHOLAR_SOURCE = "EasyScholar Open API"


class EasyScholarConfigurationError(RuntimeError):
    """Raised when the optional service is not configured."""


class EasyScholarRequestError(RuntimeError):
    """A UI-safe request error that never includes the key or full URL."""


def get_easyscholar_settings() -> dict[str, Any]:
    raw = load_app_settings().get("easyscholar", {})
    raw = raw if isinstance(raw, dict) else {}
    try:
        cache_days = max(1, min(365, int(raw.get("cache_days", 30))))
    except (TypeError, ValueError):
        cache_days = 30
    return {
        "enabled": bool(raw.get("enabled", False)),
        "secret_key_secret": str(raw.get("secret_key_secret", "")).strip(),
        "cache_days": cache_days,
    }


def easyscholar_readiness(
    settings: dict[str, Any] | None = None,
    *,
    revealer: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Return a truthful local readiness state without contacting the API.

    A saved DPAPI blob is not proof that the current Windows account can still
    decrypt it.  This happens after a settings restore or a Windows-account
    change, and previously made the UI report a misleading "configured" state.
    """

    config = get_easyscholar_settings() if settings is None else settings
    config = config if isinstance(config, dict) else {}
    enabled = bool(config.get("enabled", False))
    token = str(config.get("secret_key_secret", "")).strip()
    if not enabled:
        return {
            "ready": False,
            "state": "disabled",
            "needs_reentry": False,
            "message": "EasyScholar 尚未启用；请在“设置 → AI 与期刊数据”启用并填写密钥。",
        }
    if not token:
        return {
            "ready": False,
            "state": "missing",
            "needs_reentry": True,
            "message": "请在“设置 → AI 与期刊数据”填写 EasyScholar 密钥。",
        }
    reveal = revealer or reveal_secret
    try:
        secret = str(reveal(token) or "").strip()
    except SecretStoreError:
        return {
            "ready": False,
            "state": "unreadable",
            "needs_reentry": True,
            "message": "当前保存的 EasyScholar 密钥无法解密，请在“设置 → AI 与期刊数据”重新填写。",
        }
    if not secret:
        return {
            "ready": False,
            "state": "empty",
            "needs_reentry": True,
            "message": "当前保存的 EasyScholar 密钥为空，请重新填写。",
        }
    return {
        "ready": True,
        "state": "ready",
        "needs_reentry": False,
        "message": "EasyScholar 密钥可读取。",
    }


def is_easyscholar_ready() -> bool:
    return bool(easyscholar_readiness().get("ready"))


def _require_secret() -> tuple[dict[str, Any], str]:
    config = get_easyscholar_settings()
    if not config["enabled"]:
        raise EasyScholarConfigurationError("请先在“设置 → AI 与期刊数据”启用 EasyScholar。")
    if not config["secret_key_secret"]:
        raise EasyScholarConfigurationError("请先在设置中填写 EasyScholar 开放接口密钥。")
    try:
        secret = reveal_secret(config["secret_key_secret"])
    except SecretStoreError as error:
        raise EasyScholarConfigurationError(
            "当前保存的 EasyScholar 密钥无法解密，请在“设置 → AI 与期刊数据”重新填写。"
        ) from error
    if not secret:
        raise EasyScholarConfigurationError("请先在设置中填写 EasyScholar 开放接口密钥。")
    return config, secret


def _date_key(value: str | date | None) -> str:
    if isinstance(value, date):
        return value.isoformat()
    candidate = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return date.today().isoformat()


def _parse_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def journal_easyscholar_signature(journal: dict[str, Any]) -> str:
    """Fingerprint only identity fields that change an external lookup."""
    journal = journal if isinstance(journal, dict) else {}
    payload = {
        "name": " ".join(str(journal.get("name", "")).casefold().split()),
        "issn": "".join(str(journal.get("issn", "")).casefold().split()),
        "publisher": " ".join(str(journal.get("publisher", "")).casefold().split()),
    }
    packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(packed.encode("utf-8")).hexdigest()[:32]


def journal_needs_easyscholar_update(
    journal: dict[str, Any], *, today: str | date | None = None, cache_days: int = 30
) -> bool:
    """Return true only for new, changed or stale journal identities."""
    journal = journal if isinstance(journal, dict) else {}
    metadata = journal.get("easyscholar", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    current_signature = journal_easyscholar_signature(journal)
    if not metadata or str(metadata.get("query_signature", "")) != current_signature:
        return True
    checked_at = _parse_date(metadata.get("checked_at"))
    today_date = _parse_date(_date_key(today)) or date.today()
    try:
        cache_days = max(1, min(365, int(cache_days)))
    except (TypeError, ValueError):
        cache_days = 30
    return checked_at is None or (today_date - checked_at).days >= cache_days


def _rank_mapping(payload: Any) -> dict[str, Any]:
    """Find the rank object across minor response-shape changes."""
    if not isinstance(payload, dict):
        return {}
    candidate: Any = payload
    for key in ("data", "officialRank", "all"):
        if isinstance(candidate, dict) and key in candidate:
            candidate = candidate[key]
    if isinstance(candidate, dict):
        return candidate

    def walk(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            lower = {str(key).casefold() for key in value}
            if {"sci", "ssci", "sciup", "scibase"} & lower:
                return value
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found:
                    return found
        return {}

    return walk(payload)


def _value(mapping: dict[str, Any], *aliases: str) -> Any:
    folded = {str(key).casefold(): value for key, value in mapping.items()}
    for alias in aliases:
        if alias.casefold() in folded:
            return folded[alias.casefold()]
    return ""


def _clean(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def _quartile(value: Any) -> str:
    text = str(value or "").upper()
    match = re.search(r"\bQ\s*([1-4])\b|([1-4])\s*区", text)
    if match is None:
        return ""
    return f"Q{match.group(1) or match.group(2)}"


def _yes(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y", "是", "ei", "included"}


def parse_easyscholar_rank_payload(payload: Any, *, checked_at: str | date | None = None) -> dict[str, Any]:
    """Normalize JCR and CAS metrics while retaining their public-source label."""
    ranks = _rank_mapping(payload)
    sci = _quartile(_value(ranks, "sci"))
    ssci = _quartile(_value(ranks, "ssci"))
    quartiles = [value for value in (sci, ssci) if value]
    metrics = [{"quartile": value, "year": 0, "category": "", "jif": "", "rank": "", "total": ""} for value in dict.fromkeys(quartiles)]
    checked = _date_key(checked_at)
    jcr = {
        "status": "verified" if metrics else "not_found",
        "source": EASYSCHOLAR_SOURCE,
        "checked_at": checked,
        "metrics": metrics,
        "confidence": "",
        "note": "" if metrics else "EasyScholar 未返回可识别的 JCR Q1–Q4 分区。",
    }
    easyscholar = {
        "source": EASYSCHOLAR_SOURCE,
        "checked_at": checked,
        "query_signature": "",
        "cas_upgrade": _clean(_value(ranks, "sciUp", "sci_up")),
        "cas_basic": _clean(_value(ranks, "sciBase", "sci_base")),
        "cas_upgrade_top": _clean(_value(ranks, "sciUpTop", "sci_up_top")),
        "cas_upgrade_small": _clean(_value(ranks, "sciUpSmall", "sci_up_small")),
        "impact_factor": _clean(_value(ranks, "sciif", "impactFactor", "if"), 40),
        "impact_factor_5y": _clean(_value(ranks, "sciif5", "impactFactor5", "if5"), 40),
        "ei": _yes(_value(ranks, "eii", "ei")),
        "esci": _yes(_value(ranks, "esci")),
        "jci": _clean(_value(ranks, "jci"), 40),
        "raw_status": _clean(_value(payload, "message", "msg"), 160),
    }
    return {"jcr": jcr, "easyscholar": easyscholar}


def _preserves_current_jcr(journal: dict[str, Any]) -> bool:
    current = journal.get("jcr", {})
    current = current if isinstance(current, dict) else {}
    status = str(current.get("status", "")).strip().casefold()
    source = str(current.get("source", "")).strip().casefold()
    return bool(journal.get("jcr_locked", False)) or status == "manual" or (
        status == "verified" and source and EASYSCHOLAR_SOURCE.casefold() not in source
    )


def merge_easyscholar_patch(
    journal: dict[str, Any], patch: dict[str, Any], *, query_signature: str | None = None
) -> dict[str, Any]:
    """Merge secondary data without replacing manual or Clarivate JCR data."""
    result = deepcopy(journal if isinstance(journal, dict) else {})
    patch = patch if isinstance(patch, dict) else {}
    metadata = deepcopy(patch.get("easyscholar", {})) if isinstance(patch.get("easyscholar", {}), dict) else {}
    metadata["query_signature"] = str(query_signature or journal_easyscholar_signature(result))
    result["easyscholar"] = metadata
    incoming_jcr = patch.get("jcr", {})
    incoming_jcr = incoming_jcr if isinstance(incoming_jcr, dict) else {}
    if incoming_jcr and not _preserves_current_jcr(result):
        result["jcr"] = deepcopy(incoming_jcr)
    return result


def _request_rank_payload(journal: dict[str, Any], secret_key: str) -> dict[str, Any]:
    name = _clean(journal.get("name"), 220)
    if not name:
        raise EasyScholarRequestError("该期刊缺少刊名，无法查询期刊数据。")
    query = urlencode({"secretKey": secret_key, "publicationName": name})
    request = Request(
        f"{EASYSCHOLAR_OPEN_ENDPOINT}?{query}",
        headers={"Accept": "application/json", "User-Agent": "ScientificAssistant/11"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=25) as response:  # noqa: S310 - opt-in user-configured public endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise EasyScholarRequestError(f"EasyScholar 查询失败（HTTP {error.code}）。") from error
    except (URLError, TimeoutError) as error:
        raise EasyScholarRequestError("无法连接 EasyScholar，请检查网络或密钥状态。") from error
    except json.JSONDecodeError as error:
        raise EasyScholarRequestError("EasyScholar 未返回可识别的 JSON 数据。") from error
    return payload if isinstance(payload, dict) else {}


def _ensure_success_payload(payload: dict[str, Any]) -> None:
    """Reject a provider-level error before storing it as a negative lookup.

    EasyScholar documents ``code=200`` / ``msg=SUCCESS`` for a successful
    rank response.  Some installations return ``0`` or omit the envelope, so
    accept those compatible success forms as well; any explicit non-success
    code is a configuration/request problem rather than a real "not found".
    """
    if not isinstance(payload, dict):
        raise EasyScholarRequestError("EasyScholar 未返回可识别的期刊数据。")
    if "code" not in payload:
        return
    code = str(payload.get("code", "")).strip().casefold()
    if code in {"", "0", "200", "success"}:
        return
    raise EasyScholarRequestError("EasyScholar 未完成查询，请检查密钥、期刊名或接口配额。")


def fetch_easyscholar_metrics(
    journal: dict[str, Any], *, secret_key: str | None = None, request_json: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    today: str | date | None = None,
) -> dict[str, Any]:
    """Fetch one journal payload; injected request functions keep tests offline."""
    if secret_key is None:
        _config, secret_key = _require_secret()
    secret_key = str(secret_key or "").strip()
    if not secret_key:
        raise EasyScholarConfigurationError("请先在设置中填写 EasyScholar 开放接口密钥。")
    try:
        payload = request_json(journal, secret_key) if request_json is not None else _request_rank_payload(journal, secret_key)
    except EasyScholarRequestError:
        raise
    except Exception as error:  # noqa: BLE001 - do not leak provider or key text
        raise EasyScholarRequestError("EasyScholar 数据未更新，请稍后重试。") from error
    _ensure_success_payload(payload)
    return parse_easyscholar_rank_payload(payload, checked_at=_date_key(today))


def enrich_journals_with_easyscholar(
    journals: list[dict[str, Any]], *, secret_key: str | None = None, cache_days: int | None = None,
    request_json: Callable[[dict[str, Any], str], dict[str, Any]] | None = None, today: str | date | None = None,
) -> dict[str, Any]:
    """Update only journal identities that are new, changed or cache-stale."""
    config: dict[str, Any] | None = None
    if secret_key is None:
        config, secret_key = _require_secret()
    secret_key = str(secret_key or "").strip()
    if not secret_key:
        raise EasyScholarConfigurationError("请先在设置中填写 EasyScholar 开放接口密钥。")
    if cache_days is None:
        cache_days = int(config.get("cache_days", 30)) if config else 30
    today_key = _date_key(today)
    updated = [deepcopy(item) for item in journals if isinstance(item, dict)]
    changed = 0
    requested = 0
    skipped = 0
    errors: list[str] = []
    for index, journal in enumerate(updated):
        if not journal_needs_easyscholar_update(journal, today=today_key, cache_days=cache_days):
            skipped += 1
            continue
        requested += 1
        if request_json is None and requested > 1:
            time.sleep(0.35)
        name = _clean(journal.get("name"), 120) or "未命名期刊"
        try:
            patch = fetch_easyscholar_metrics(
                journal, secret_key=secret_key, request_json=request_json, today=today_key
            )
            merged = merge_easyscholar_patch(journal, patch, query_signature=journal_easyscholar_signature(journal))
        except (EasyScholarConfigurationError, EasyScholarRequestError):
            errors.append(f"{name}：EasyScholar 数据未更新。")
            continue
        if merged != journal:
            updated[index] = merged
            changed += 1
    return {"journals": updated, "changed": changed, "requested": requested, "skipped": skipped, "errors": errors}
