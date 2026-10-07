"""Sync HomeMind API error codes into the dashboard locale bundles.

The repo's i18n rule is that every backend ``ErrorCode`` has a matching
``apiErrors`` entry in both ``en.json`` and ``zh.json``. This script
reads the canonical bilingual copy from
:mod:`homemind.infra.errors` and writes the missing keys, preserving
whatever formatting the surrounding file uses.

Idempotent: running it twice changes nothing the second time.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from homemind.infra.errors import DEFAULT_MESSAGES, HomeMindErrorCode  # noqa: E402

LOCALES = {
    "en": ROOT / "dashboard" / "src" / "locales" / "en.json",
    "zh": ROOT / "dashboard" / "src" / "locales" / "zh.json",
}
INDEX = {"zh": 0, "en": 1}


def main() -> int:
    changed: list[str] = []
    for locale, path in LOCALES.items():
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        bucket = data.setdefault("apiErrors", {})
        added = 0
        for code in HomeMindErrorCode:
            if code.value in bucket:
                continue
            bucket[code.value] = DEFAULT_MESSAGES[code][INDEX[locale]]
            added += 1
        if added:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            changed.append(f"{path.name}: +{added}")
    for entry in changed:
        print(entry)
    if not changed:
        print("locale bundles already in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
