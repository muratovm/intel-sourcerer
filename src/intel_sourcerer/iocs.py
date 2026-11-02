"""Pull CVE IDs, ATT&CK IDs, file hashes and file names out of article text, without any network lookups."""
from functools import lru_cache
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


BLOCKS = ['p', 'li', 'td', 'th', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'figcaption', 'dd', 'dt', 'caption', 'summary', 'div']


@lru_cache(maxsize=1)
def _attack_names():
    """MITRE's names, bundled by update_attack_names so nothing is looked up online."""
    try:
        data = json.loads(Path(__file__).with_name('attack_names.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}, frozenset()
    return data.get('names', {}), frozenset(data.get('retired', []))


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


def extract_iocs(html):
    """Return {'cves', 'techniques', 'hashes', 'files'}, each a list of {value, ...} in order of first appearance."""
    soup = BeautifulSoup(html or '', 'html.parser')
    for tag in soup.select('.article-math, [data-reading-exclude], blockquote > p > a'):
        tag.decompose()
    cves, techniques, hashes, files = {}, {}, {}, {}

    def add_cve(match):
        value = f'CVE-{match.group(1)}-{match.group(2)}'
        cves.setdefault(value, {'value': value})

    def add_technique(raw):
        value = raw.upper().replace('/', '.')
        if value not in techniques:
            techniques[value] = _technique(value)

    for block in soup.find_all(BLOCKS):
        if block.find(BLOCKS):
            continue
        text = URL.sub(' ', block.get_text(' '))
        for match in CVE.finditer(text):
            add_cve(match)
        for match in ATTACK.finditer(text):
            add_technique(match.group(1))
        for match in HASH.finditer(text):
            value = match.group(0).lower()
            if _is_hash(value):
                hashes.setdefault(value, {'value': value, 'type': HASH_TYPES[len(value)]})
        for match in FILE_NAME.finditer(text):
            name = _clean_name(match.group(1))
            if _is_file_name(name):
                files.setdefault(name.casefold(), {'value': name, 'common': name.casefold() in COMMON_FILES})
    # Reports often link a technique's name to its ATT&CK page, with no ID in the text.
    for link in soup.find_all('a', href=True):
        for match in CVE.finditer(link['href']):
            add_cve(match)
        for match in ATTACK_LINK.finditer(link['href']):
            add_technique(match.group(1))
    return {
        'cves': list(cves.values()),
        'techniques': list(techniques.values()),
        'hashes': list(hashes.values()),
        'files': list(files.values()),
    }


def _technique_line(item):
    name = f" {item['name']}" if item.get('name') else ''
    return f"- {item['value']}{name}{' (retired by MITRE)' if item.get('retired') else ''}"


def iocs_markdown(iocs):
    """An export section with samples first; common tools are listed apart so they aren't hunted."""
    lines = []
    if iocs['cves']:
        lines += ['### CVEs', ''] + [f"- {item['value']}" for item in iocs['cves']] + ['']
    if iocs['techniques']:
        lines += ['### MITRE ATT&CK', ''] + [_technique_line(item) for item in iocs['techniques']] + ['']
    if iocs['hashes']:
        lines += ['### File hashes', ''] + [f"- {item['type']}: `{item['value']}`" for item in iocs['hashes']] + ['']
    samples = [item for item in iocs['files'] if not item['common']]
    common = [item for item in iocs['files'] if item['common']]
    if samples:
        lines += ['### File names', ''] + [f"- `{item['value']}`" for item in samples] + ['']
    if common:
        lines += ['### Common tools and files mentioned', ''] + [f"- `{item['value']}`" for item in common] + ['']
    return '## Indicators\n\n' + '\n'.join(lines) + '\n' if lines else ''
