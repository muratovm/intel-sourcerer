"""Rebuild attack_names.json from MITRE's published data; the app itself never goes online for names.

Run: uv run --with pyyaml python -m intel_sourcerer.update_attack_names
"""
import json
from pathlib import Path
import re

import httpx

OUTPUT = Path(__file__).with_name('attack_names.json')
STIX_BASE = 'https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master'
# Enterprise comes first, so it wins if a domain ever reuses an ID.
STIX_SOURCES = {
    'enterprise': f'{STIX_BASE}/enterprise-attack/enterprise-attack.json',
    'mobile': f'{STIX_BASE}/mobile-attack/mobile-attack.json',
    'ics': f'{STIX_BASE}/ics-attack/ics-attack.json',
}
ATLAS_SOURCE = 'https://raw.githubusercontent.com/mitre-atlas/atlas-data/main/dist/ATLAS.yaml'
VALID_ID = re.compile(r'^(T\d{4}(\.\d{3})?|TA\d{4}|AML\.TA?\d{4}(\.\d{3})?)$')
MAX_NAME = 200


def _clean_name(value):
    name = ' '.join(value.split()) if isinstance(value, str) else ''
    return name if 0 < len(name) <= MAX_NAME and '<' not in name else ''


def stix_entries(bundle):
    """Yield (id, name, retired) from an ATT&CK STIX bundle; sub-techniques get their parent's name in front."""
    objects = [item for item in bundle.get('objects', []) if item.get('type') in {'attack-pattern', 'x-mitre-tactic'}]
    found = []
    for item in objects:
        reference = next((ref for ref in item.get('external_references', []) if ref.get('source_name', '').startswith('mitre-') and ref.get('external_id')), None)
        identifier = reference['external_id'] if reference else ''
        name = _clean_name(item.get('name'))
        if VALID_ID.match(identifier) and name:
            found.append((identifier, name, bool(item.get('revoked') or item.get('x_mitre_deprecated'))))
    names = {identifier: name for identifier, name, _ in found}
    return [
        (identifier, f"{names[identifier.split('.')[0]]}: {name}" if '.' in identifier and identifier.split('.')[0] in names else name, retired)
        for identifier, name, retired in found
    ]


def atlas_entries(data):
    """Yield (id, name, retired) from MITRE ATLAS's published YAML."""
    found = []
    for matrix in data.get('matrices', []):
        found += matrix.get('tactics', []) + matrix.get('techniques', [])
    names = {item.get('id'): _clean_name(item.get('name')) for item in found if isinstance(item, dict)}
    entries = []
    for identifier, name in names.items():
        if isinstance(identifier, str) and VALID_ID.match(identifier) and name:
            parent = names.get(identifier.rsplit('.', 1)[0]) if identifier.count('.') == 2 else None
            entries.append((identifier, f'{parent}: {name}' if parent else name, False))
    return entries


def build(entries):
    """Live names win over retired ones, and earlier sources win ties."""
    names, retired = {}, set()
    for identifier, name, is_retired in entries:
        if identifier not in names or (identifier in retired and not is_retired):
            names[identifier] = name
            retired.discard(identifier)
            if is_retired:
                retired.add(identifier)
    return {'names': dict(sorted(names.items())), 'retired': sorted(retired)}


def bundle_version(bundle):
    return next((item.get('x_mitre_version') for item in bundle.get('objects', []) if item.get('type') == 'x-mitre-collection'), None)


def main():
    import yaml

    entries, versions = [], {}
    with httpx.Client(timeout=180, follow_redirects=True, trust_env=False) as client:
        for domain, url in STIX_SOURCES.items():
            response = client.get(url)
            response.raise_for_status()
            bundle = response.json()
            versions[domain] = bundle_version(bundle)
            entries += stix_entries(bundle)
        response = client.get(ATLAS_SOURCE)
        response.raise_for_status()
        atlas = yaml.safe_load(response.text)
        versions['atlas'] = atlas.get('version')
        entries += atlas_entries(atlas)
    result = {'versions': versions, **build(entries)}
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=0) + '\n', encoding='utf-8')
    print(json.dumps({'versions': versions, 'names': len(result['names']), 'retired': len(result['retired'])}))


if __name__ == '__main__':
    main()
