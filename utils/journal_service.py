"""Network enrichment for the local journal library, without API keys."""

from __future__ import annotations

import json
import re
from datetime import date
from difflib import SequenceMatcher
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from utils.publisher_utils import fuzzy_publisher_check


CROSSREF_API = "https://api.crossref.org"
USER_AGENT = "ResearchAssistant/0.6 (personal desktop research tool)"


class JournalLookupError(RuntimeError):
    pass


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def journal_metadata_source_signature(journal: dict) -> str:
    """A canonical-title fingerprint for inexpensive incremental Crossref lookups."""
    return f"v1:{_key(str(journal.get('name', '')))}"


def journal_needs_metadata_enrichment(journal: dict) -> bool:
    """Only new/renamed/incomplete entries need another public metadata query."""
    if not str(journal.get("metadata_updated_at", "")).strip():
        return True
    if not str(journal.get("issn", "")).strip() or not str(journal.get("publisher", "")).strip():
        return True
    return str(journal.get("metadata_source_signature", "")).strip() != journal_metadata_source_signature(journal)


def _request(path: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urlencode({name: value for name, value in params.items() if value not in {None, ""}})
    request = Request(
        f"{CROSSREF_API}{path}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise JournalLookupError(str(error)) from error


def lookup_journal(name: str) -> dict[str, str]:
    """Resolve a journal name to Crossref's canonical name, publisher and ISSN."""
    wanted = _key(name)
    if not wanted:
        raise JournalLookupError("期刊名称为空")
    # Very short titles (for example, SOIL or Land) need a larger candidate
    # set so an exact title is never replaced by a similar word such as Landing.
    payload = _request("/journals", {"query": name, "rows": 100 if len(wanted) <= 5 else 20})
    items = payload.get("message", {}).get("items", []) if isinstance(payload, dict) else []
    best: tuple[float, dict] | None = None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        candidate = _key(title)
        if not candidate:
            continue
        if candidate == wanted:
            best = (1.0, item)
            break
        score = 1.0 if candidate == wanted else SequenceMatcher(None, wanted, candidate).ratio()
        if best is None or score > best[0]:
            best = (score, item)
    if best is None or best[0] < 0.72 or (len(wanted) <= 5 and best[0] < 1.0):
        raise JournalLookupError("未找到可靠的期刊匹配")
    item = best[1]
    issns = item.get("ISSN", [])
    issn = str(issns[0]).strip() if isinstance(issns, list) and issns else ""
    return {
        "name": str(item.get("title", name)).strip() or name,
        "publisher": str(item.get("publisher", "")).strip(),
        "issn": issn,
        "metadata_updated_at": date.today().isoformat(),
    }


def enrich_journal_library(journals: list[dict]) -> dict[str, Any]:
    """Look up every local journal and return updated records plus nonfatal errors."""
    updated: list[dict] = []
    changed = 0
    errors: list[str] = []
    for journal in journals:
        item = dict(journal)
        name = str(item.get("name", "")).strip()
        if not name:
            updated.append(item)
            continue
        try:
            metadata = lookup_journal(name)
        except JournalLookupError as error:
            errors.append(f"{name}：{error}")
            updated.append(item)
            continue
        recorded_publisher = str(item.get("publisher", "")).strip()
        observed_publisher = str(metadata.get("publisher", "")).strip()
        validation = fuzzy_publisher_check(recorded_publisher, observed_publisher)
        validation.update({"source": "Crossref", "checked_at": str(metadata.get("metadata_updated_at", "")).strip()})
        item["publisher_validation"] = validation
        before = (item.get("name", ""), item.get("publisher", ""), item.get("issn", ""))
        item["name"] = metadata["name"]
        if metadata["publisher"]:
            # Keep a user-entered publisher when Crossref disagrees.  The
            # external value remains in publisher_validation for review; this
            # prevents a later refresh from erasing the mismatch signal.
            if not recorded_publisher or validation["status"] in {"match", "probable"}:
                item["publisher"] = metadata["publisher"]
        if metadata["issn"]:
            item["issn"] = metadata["issn"]
        item["metadata_updated_at"] = metadata["metadata_updated_at"]
        item["metadata_source_signature"] = journal_metadata_source_signature(item)
        after = (item.get("name", ""), item.get("publisher", ""), item.get("issn", ""))
        if before != after:
            changed += 1
        updated.append(item)
    return {"journals": updated, "changed": changed, "errors": errors}
