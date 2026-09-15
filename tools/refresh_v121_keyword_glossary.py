"""Apply vetted v12.1 scientific translations to fields that were blank before migration."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from utils.local_translation_backend import glossary_translation


def main() -> int:
    path = Path(sys.argv[1]).resolve()
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    changed = 0
    for field in ("terms", "pending_terms", "excluded_entries"):
        for item in payload.get(field, []) if isinstance(payload.get(field), list) else []:
            if not isinstance(item, dict):
                continue
            translated = glossary_translation(str(item.get("canonical_en", item.get("text", ""))))
            if translated and str(item.get("translation_zh", "")) != translated:
                item["translation_zh"] = translated
                item["translation_source"] = "v12.1_scientific_glossary"
                changed += 1
    temporary = path.with_suffix(path.suffix + ".v121-glossary.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(temporary.read_text(encoding="utf-8"))
    os.replace(temporary, path)
    print(json.dumps({"changed": changed, "path": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
