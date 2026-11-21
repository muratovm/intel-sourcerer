"""Pull CVEs, ATT&CK IDs, hashes, network IOCs, files, and registry keys out of article text, without any network lookups."""
from functools import lru_cache
import ipaddress
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

# Sites often print non-breaking hyphens, which copy badly and must not hide the ID.
CVE = re.compile(r'(?<![\w-])CVE[-\u2010\u2011\u2012\u2013\u2014]((?:19|20)\d{2})[-\u2010\u2011\u2012\u2013\u2014](\d{4,7})(?![\w-])', re.I)
# Real ATT&CK ranges only: enterprise and mobile T1001+, ICS T08xx, tactics TA00xx/TA01xx.
# That excludes "T1000" and threat-actor names like TA453.
# ATLAS, MITRE's AI attack framework, always carries its AML. prefix.
ATTACK = re.compile(r'(?<![\w.-])(T1(?!000)\d{3}(?:[./]\d{3})?|T08\d{2}|TA0[01]\d{2}|AML\.TA?\d{4}(?:\.\d{3})?)(?![\w-])')
ATTACK_LINK = re.compile(r'attack\.mitre\.org/(?:techniques|tactics)/(T\d{4}(?:/\d{3})?|TA\d{4})', re.I)
# MITRE Groups (G0xxx) and Software (S0xxx/S1xxx).
ATTACK_ENTITY = re.compile(r'(?<![\w.-])([GS][01]\d{3})(?![\w-])')
ATTACK_ENTITY_LINK = re.compile(r'attack\.mitre\.org/(?:groups|software)/([GS]\d{4})', re.I)
HASH_TYPES = {32: 'MD5', 40: 'SHA-1', 64: 'SHA-256', 128: 'SHA-512'}
HASH = re.compile(r'(?<![0-9a-zA-Z])(?:[0-9a-fA-F]{128}|[0-9a-fA-F]{64}|[0-9a-fA-F]{40}|[0-9a-fA-F]{32})(?![0-9a-zA-Z])')
# Extensions that name a file an analyst would act on. .com and .net read as domains, so they stay out.
EXTENSIONS = (
    'exe|dll|sys|scr|cpl|ocx|msi|msp|bat|cmd|ps1|psm1|vbs|vbe|jse|wsf|wsh|hta|lnk|pif|'
    'js|jar|py|pyc|sh|bash|php|asp|aspx|jsp|elf|so|dylib|bin|dat|tmp|apk|ipa|dmg|pkg|deb|rpm|plist|'
    'zip|rar|7z|tar|gz|tgz|iso|img|vhd|vhdx|cab|'
    'doc|docx|docm|xls|xlsx|xlsm|ppt|pptx|pptm|rtf|pdf|'
    'yar|yara|ini|cfg|conf|log|xml|json|yaml|yml|txt|csv'
)
# Defanged dots count, since reports write evil[.]exe so nobody clicks it.
FILE_NAME = re.compile(
    r'(?<![\w@.-])([A-Za-z0-9_$%~+-][\w$%~+ -]{0,80}?(?:(?:\.|\[\.\]|\(\.\))[\w-]{1,40}){0,3}(?:\.|\[\.\]|\(\.\))(?:' + EXTENSIONS + r'))(?![\w-])(?!\.\w)',
    re.I,
)
URL = re.compile(r'\b(?:https?|ftp)://\S+', re.I)
# Explicitly defanged URLs (e.g. hxxps://evil[.]com/c2 or http[s]://1[.]2[.]3[.]4/payload).
DEFANGED_URL = re.compile(
    r'\b((?:hxxps?|fxp|https?)(?:\[s\])?(?::|\[:\])//[^\s<>"\')]+)',
    re.I,
)
# IPv4 addresses (including defanged 198[.]51[.]100[.]1). Exclude version prefixes like v1.2.3.4.
IPV4 = re.compile(
    r'(?<![\w.-])((?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:(?:\.|\[\.\]|\(\.\))(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})(?![\w.-])'
)
# Defanged domains (any valid TLD) or fanged domains with high-signal TLDs.
DOMAIN_TLDS = (
    'com|net|org|io|co|info|biz|xyz|top|ru|cn|su|tk|ml|ga|cf|gq|pw|cc|ws|tv|me|in|de|uk|fr|nl|pl|ua|ir|kp|'
    'online|site|club|live|shop|tech|cloud|pro|vip|icu|buzz|cyou|sbs|click|link|work|support|services|digital|'
    'app|dev|ai|gg|to|sh|ly|onion|bit'
)
DOMAIN = re.compile(
    r'(?<![\w@/.-])([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:(?:\.|\[\.\]|\(\.\))[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)(?![\w@.-])',
    re.I,
)
# Windows registry paths.
REGISTRY = re.compile(
    r'\b((?:HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER|HKEY_CLASSES_ROOT|HKEY_USERS|HKEY_CURRENT_CONFIG|HKLM|HKCU|HKCR|HKU|HKCC)\\[^\s<>"\',;)\]]{3,220})',
    re.I,
)

# Product and language names that only look like files.
# Names every report mentions: useful to see, but not worth hunting on.
COMMON_FILES = {
    'cmd.exe', 'powershell.exe', 'pwsh.exe', 'wscript.exe', 'cscript.exe', 'mshta.exe', 'msiexec.exe', 'rundll32.exe',
    'regsvr32.exe', 'reg.exe', 'net.exe', 'net1.exe', 'netsh.exe', 'schtasks.exe', 'sc.exe', 'taskkill.exe', 'tasklist.exe',
    'whoami.exe', 'wmic.exe', 'vssadmin.exe', 'certutil.exe', 'bitsadmin.exe', 'conhost.exe', 'explorer.exe', 'lsass.exe',
    'svchost.exe', 'services.exe', 'winlogon.exe', 'csrss.exe', 'smss.exe', 'dllhost.exe', 'w3wp.exe', 'ntdsutil.exe',
    'eventcreate.exe', 'control.exe', 'sfc.exe', 'ssh.exe', 'python.exe', 'pythonw.exe', 'java.exe', 'javaw.exe', 'psexec.exe', 'psexec64.exe',
    'kernel32.dll', 'ntdll.dll', 'user32.dll', 'advapi32.dll', 'shell32.dll', 'amsi.dll', 'ws2_32.dll', 'version.dll', 'netio.sys', 'tcpip.sys',
    'requirements.txt', 'package.json', 'package-lock.json', 'license.txt', 'readme.txt', 'readme.md', 'config.yaml', 'config.xml', 'config.json',
    'wp-config.php', 'php.ini', 'httpd.conf', 'main.py', 'server.py', 'server.js', 'index.js', 'index.php', 'sample.exe',
}
NOT_FILES = {'node.js', 'next.js', 'react.js', 'vue.js', 'nuxt.js', 'express.js', 'three.js', 'd3.js', 'angular.js', 'deno.js', 'bun.js', 'asp.net'}

# High-frequency legitimate / documentation domains excluded from IOC domain extraction.
COMMON_DOMAINS = {
    'example.com', 'example.org', 'example.net', 'localhost.localdomain',
    'github.com', 'raw.githubusercontent.com', 'gist.github.com', 'gitlab.com',
    'microsoft.com', 'learn.microsoft.com', 'docs.microsoft.com', 'windows.com', 'office.com', 'azure.com', 'live.com',
    'google.com', 'cloud.google.com', 'blog.google', 'googleapis.com', 'youtube.com', 'youtu.be',
    'apple.com', 'amazon.com', 'amazonaws.com', 'cloudflare.com', 'akamai.com',
    'mitre.org', 'attack.mitre.org', 'cve.mitre.org', 'nvd.nist.gov', 'cisa.gov',
    'virustotal.com', 'shodan.io', 'censys.io', 'abuse.ch', 'bazaar.abuse.ch', 'urlhaus.abuse.ch',
    'twitter.com', 'x.com', 'linkedin.com', 'reddit.com', 'wikipedia.org', 'medium.com', 'substack.com',
    'openai.com', 'anthropic.com', 'sentinelone.com', 'crowdstrike.com', 'paloaltonetworks.com',
    'talosintelligence.com', 'mandiant.com', 'securelist.com', 'welivesecurity.com', 'checkpoint.com',
    'rapid7.com', 'sophos.com', 'proofpoint.com', 'recordedfuture.com', 'redcanary.com', 'huntress.com',
    'trendmicro.com', 'elastic.co', 'bleepingcomputer.com', 'thehackernews.com', 'krebsonsecurity.com',
    'arxiv.org', 'doi.org', 'w3.org', 'ietf.org', 'rfc-editor.org', 'schema.org',
    'python.org', 'pypi.org', 'npmjs.com', 'docker.com', 'debian.org', 'ubuntu.com',
    'zoom.us', 'slack.com', 'discord.com', 'telegram.org', 't.me', 'dropbox.com',
    'bit.ly', 'tinyurl.com', 'pastebin.com', 'wordpress.com', 'blogspot.com',
}
TLD_SET = frozenset(DOMAIN_TLDS.split('|'))
FILE_EXT_SET = frozenset(EXTENSIONS.split('|'))

# RFC 5737 / documentation and non-routable IPv4 networks to ignore as IOCs.
DOC_IP_NETWORKS = (
    ipaddress.ip_network('192.0.2.0/24'),
    ipaddress.ip_network('198.51.100.0/24'),
    ipaddress.ip_network('203.0.113.0/24'),
)

BLOCKS = ['p', 'li', 'td', 'th', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'figcaption', 'dd', 'dt', 'caption', 'summary', 'div']


@lru_cache(maxsize=1)
def _attack_names():
    """MITRE's names, bundled by update_attack_names so nothing is looked up online."""
    try:
        data = json.loads(Path(__file__).with_name('attack_names.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}, frozenset()
    return data.get('names', {}), frozenset(data.get('retired', []))


@lru_cache(maxsize=1)
def _kev_entries():
    """CISA KEV entries, bundled by update_kev so nothing is looked up online."""
    try:
        data = json.loads(Path(__file__).with_name('kev.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    entries = data.get('entries', {})
    return entries if isinstance(entries, dict) else {}


def kev_info(cve_id):
    """Return CISA KEV metadata dict for a CVE if present, else None."""
    return _kev_entries().get(cve_id.upper())


def _technique(value):
    names, retired = _attack_names()
    item = {'value': value}
    if value in names:
        item['name'] = names[value]
    if value in retired:
        item['retired'] = True
    return item


def _is_hash(value):
    return len(set(value)) > 4 and not value.isdigit()


def _clean_name(raw):
    name = re.sub(r'\[\.\]|\(\.\)', '.', raw).strip()
    # Prose can't be told from a spaced file name, so only the last word counts.
    return name.rsplit(' ', 1)[-1]


def _is_file_name(name):
    stem = name.rsplit('.', 1)[0]
    # "a .jsp file" names a type, and %s is a format string in quoted code.
    return bool(stem.strip('.')) and '%s' not in name and name.casefold() not in NOT_FILES


def _refang_dots(raw):
    return re.sub(r'\[\.\]|\(\.\)', '.', raw)


def _refang_url(raw):
    cleaned = raw.rstrip('.,;:)]}>"\'')
    cleaned = re.sub(r'^hxxp(s?)', r'http\1', cleaned, flags=re.I)
    cleaned = re.sub(r'^fxp', 'ftp', cleaned, flags=re.I)
    cleaned = re.sub(r'\[s\]', 's', cleaned, flags=re.I)
    cleaned = cleaned.replace('[:]', ':')
    return _refang_dots(cleaned)


def _is_public_ioc_ip(raw):
    ip_str = _refang_dots(raw)
    try:
        addr = ipaddress.IPv4Address(ip_str)
    except ValueError:
        return None
    if not addr.is_global or any(addr in net for net in DOC_IP_NETWORKS):
        return None
    return ip_str


def _is_ioc_domain(raw, extracted_files_lower):
    defanged = '[.]' in raw or '(.)' in raw
    host = _refang_dots(raw).lower().strip('.')
    if not host or len(host) > 253 or host in extracted_files_lower or host in NOT_FILES:
        return None
    parts = host.split('.')
    if len(parts) < 2 or all(part.isdigit() for part in parts):
        return None
    tld = parts[-1]
    if tld not in TLD_SET:
        return None
    # Skip file-like names unless explicitly defanged (e.g. evil[.]io is a domain, loader.sh is a file).
    if not defanged and tld in FILE_EXT_SET and len(parts) == 2:
        return None
    # Skip benign/common domains and their subdomains.
    if host in COMMON_DOMAINS or any(host.endswith('.' + common) for common in COMMON_DOMAINS):
        return None
    # For non-defanged domains, avoid noisy generic words ending in .in/.co/.to/.me/.ai/.io unless subdomain or multi-char.
    if not defanged and len(parts) == 2 and len(parts[0]) < 4 and tld in {'in', 'to', 'me', 'co', 'ai', 'io', 'sh', 'tv', 'cc', 'ws'}:
        return None
    return host


def extract_iocs(html):
    """Return IOC groups in order of first appearance."""
    soup = BeautifulSoup(html or '', 'html.parser')
    for tag in soup.select('.article-math, [data-reading-exclude], blockquote > p > a'):
        tag.decompose()
    cves, techniques, entities, hashes, files = {}, {}, {}, {}, {}
    ips, domains, urls, registry = {}, {}, {}, {}
    names_lookup, _ = _attack_names()

    def add_cve(match):
        value = f'CVE-{match.group(1)}-{match.group(2)}'
        if value not in cves:
            item = {'value': value}
            kev = kev_info(value)
            if kev is not None:
                item['kev'] = True
                if kev.get('ransomware'):
                    item['ransomware'] = True
            cves[value] = item

    def add_technique(raw):
        value = raw.upper().replace('/', '.')
        if value not in techniques:
            techniques[value] = _technique(value)

    def add_entity(raw):
        value = raw.upper()
        # Only accept known MITRE Groups/Software (or ones from attack.mitre.org links) to avoid random G0123 prose.
        if value in names_lookup and value not in entities:
            entities[value] = _technique(value)

    for block in soup.find_all(BLOCKS):
        if block.find(BLOCKS):
            continue
        raw_text = block.get_text(' ')
        # Extract explicitly defanged URLs before stripping normal URLs.
        for match in DEFANGED_URL.finditer(raw_text):
            token = match.group(1)
            if any(marker in token.lower() for marker in ('hxxp', 'fxp', '[.]', '(.)', '[:]', '[s]')):
                refanged = _refang_url(token)
                urls.setdefault(refanged.lower(), {'value': refanged})
        text = URL.sub(' ', raw_text)
        for match in CVE.finditer(text):
            add_cve(match)
        for match in ATTACK.finditer(text):
            add_technique(match.group(1))
        for match in ATTACK_ENTITY.finditer(text):
            add_entity(match.group(1))
        for match in HASH.finditer(text):
            value = match.group(0).lower()
            if _is_hash(value):
                hashes.setdefault(value, {'value': value, 'type': HASH_TYPES[len(value)]})
        for match in FILE_NAME.finditer(text):
            name = _clean_name(match.group(1))
            if _is_file_name(name):
                files.setdefault(name.casefold(), {'value': name, 'common': name.casefold() in COMMON_FILES})
        for match in IPV4.finditer(text):
            # Ignore version numbers like "v1.2.3.4" or "version 1.2.3.4"
            start = match.start(1)
            prefix = text[max(0, start - 10):start].lower()
            if prefix.endswith('v') or 'version ' in prefix:
                continue
            ip_val = _is_public_ioc_ip(match.group(1))
            if ip_val:
                ips.setdefault(ip_val, {'value': ip_val})
        for match in REGISTRY.finditer(text):
            key = match.group(1).rstrip('.,;:')
            registry.setdefault(key.casefold(), {'value': key})
        for match in DOMAIN.finditer(text):
            dom = _is_ioc_domain(match.group(1), files)
            if dom:
                domains.setdefault(dom, {'value': dom})
    # Reports often link a technique's or group's name to its ATT&CK page, with no ID in the text.
    for link in soup.find_all('a', href=True):
        href = link['href']
        for match in CVE.finditer(href):
            add_cve(match)
        for match in ATTACK_LINK.finditer(href):
            add_technique(match.group(1))
        for match in ATTACK_ENTITY_LINK.finditer(href):
            value = match.group(1).upper()
            if value not in entities:
                entities[value] = _technique(value)
    return {
        'cves': list(cves.values()),
        'techniques': list(techniques.values()),
        'entities': list(entities.values()),
        'hashes': list(hashes.values()),
        'domains': list(domains.values()),
        'ips': list(ips.values()),
        'urls': list(urls.values()),
        'files': list(files.values()),
        'registry': list(registry.values()),
    }


def _technique_line(item):
    name = f" {item['name']}" if item.get('name') else ''
    return f"- {item['value']}{name}{' (retired by MITRE)' if item.get('retired') else ''}"


def iocs_markdown(iocs):
    """An export section with samples first; common tools are listed apart so they aren't hunted."""
    lines = []
    if iocs.get('cves'):
        lines += ['### CVEs', ''] + [
            f"- {item['value']}{' (CISA KEV)' if item.get('kev') else ''}"
            for item in iocs['cves']
        ] + ['']
    if iocs.get('techniques'):
        lines += ['### MITRE ATT&CK', ''] + [_technique_line(item) for item in iocs['techniques']] + ['']
    if iocs.get('entities'):
        lines += ['### MITRE Groups & Software', ''] + [_technique_line(item) for item in iocs['entities']] + ['']
    if iocs.get('hashes'):
        lines += ['### File hashes', ''] + [f"- {item['type']}: `{item['value']}`" for item in iocs['hashes']] + ['']
    if iocs.get('domains'):
        lines += ['### Domains', ''] + [f"- `{item['value']}`" for item in iocs['domains']] + ['']
    if iocs.get('ips'):
        lines += ['### IP addresses', ''] + [f"- `{item['value']}`" for item in iocs['ips']] + ['']
    if iocs.get('urls'):
        lines += ['### Defanged URLs', ''] + [f"- `{item['value']}`" for item in iocs['urls']] + ['']
    files = iocs.get('files', [])
    samples = [item for item in files if not item.get('common')]
    common = [item for item in files if item.get('common')]
    if samples:
        lines += ['### File names', ''] + [f"- `{item['value']}`" for item in samples] + ['']
    if iocs.get('registry'):
        lines += ['### Registry keys', ''] + [f"- `{item['value']}`" for item in iocs['registry']] + ['']
    if common:
        lines += ['### Common tools and files mentioned', ''] + [f"- `{item['value']}`" for item in common] + ['']
    return '## Indicators\n\n' + '\n'.join(lines) + '\n' if lines else ''
