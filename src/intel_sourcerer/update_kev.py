"""Rebuild kev.json from CISA's Known Exploited Vulnerabilities catalog; the app never goes online for lookups.

Run: uv run python -m intel_sourcerer.update_kev
"""
import json
from pathlib import Path
import re

import httpx

OUTPUT = Path(__file__).with_name("kev.json")
CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
VALID_CVE = re.compile(r"^CVE-(?:19|20)\d{2}-\d{4,7}$")
MAX_TEXT = 200


def _clean(value):
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return text[:MAX_TEXT].replace("<", "") if text else ""


def parse_kev(payload):
    """Extract a compact CVE -> metadata lookup from CISA KEV JSON."""
    entries = {}
    for item in payload.get("vulnerabilities", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        cve = str(item.get("cveID", "")).strip().upper()
        if not VALID_CVE.match(cve):
            continue
        entry = {}
        name = _clean(item.get("vulnerabilityName") or item.get("product"))
        if name:
            entry["name"] = name
        added = _clean(item.get("dateAdded"))
        if added:
            entry["added"] = added
        if str(item.get("knownRansomwareCampaignUse", "")).strip().lower() == "known":
            entry["ransomware"] = True
        entries[cve] = entry
    return {
        "version": _clean(payload.get("catalogVersion")) if isinstance(payload, dict) else "",
        "entries": dict(sorted(entries.items())),
    }


def main():
    with httpx.Client(timeout=60, follow_redirects=True, trust_env=False) as client:
        response = client.get(CISA_KEV_URL)
        response.raise_for_status()
        data = parse_kev(response.json())
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"version": data["version"], "count": len(data["entries"])}))


if __name__ == "__main__":
    main()
