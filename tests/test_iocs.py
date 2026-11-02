import pytest

from intel_sourcerer.iocs import extract_iocs, iocs_markdown

SHA256 = '9f1f11a708d393e0a4109ae189bc64f1f3e312653dcf317a2bd406f18ffcc507'
MD5 = '2915b3f8b703eb744fc54c81f4a9c67f'
SHA1 = 'da39a3ee5e6b4b0d3255bfef95601890afd80709'
SHA512 = 'cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e'


EMPTY = {'cves': [], 'techniques': [], 'hashes': [], 'files': []}


def values(html, kind):
    return [item['value'] for item in extract_iocs(html)[kind]]


def hashes(html):
    return [(item['type'], item['value']) for item in extract_iocs(html)['hashes']]


def files(html):
    return [item['value'] for item in extract_iocs(html)['files']]


def test_all_hash_types_are_labelled_and_normalised():
    html = f'<p>SHA256: {SHA256.upper()} MD5: {MD5}</p><ul><li>{SHA1}</li></ul><pre><code>{SHA512}</code></pre>'
    assert hashes(html) == [('SHA-256', SHA256), ('MD5', MD5), ('SHA-1', SHA1), ('SHA-512', SHA512)]


def test_hashes_in_a_table_are_found_once_each():
    html = f'<table><tr><td>{SHA256}</td><td>loader.exe</td></tr><tr><td>{SHA256}</td><td>loader.exe</td></tr></table>'
    result = extract_iocs(html)
    assert len(result['hashes']) == 1 and [item['value'] for item in result['files']] == ['loader.exe']


@pytest.mark.parametrize('text', [
    '0' * 64,
    '12345678901234567890123456789012',
    f'{SHA256}a',
    f'x{MD5}',
    '9f1f11a708d393e0a4109ae1',
    '550e8400-e29b-41d4-a716-446655440000',
    f'https://www.virustotal.com/gui/file/{SHA256}',
])
def test_things_that_are_not_hashes_are_skipped(text):
    assert hashes(f'<p>{text}</p>') == []


@pytest.mark.parametrize('text,expected', [
    ('The loader drops msedgeupdate_v3.exe on disk.', ['msedgeupdate_v3.exe']),
    ('A file named evil[.]exe and invoice(.)pdf arrived.', ['evil.exe', 'invoice.pdf']),
    (r'It wrote C:\\Users\\Public\\stage2.dll to disk.', ['stage2.dll']),
    ('It ran /tmp/.x/kworker.sh next.', ['kworker.sh']),
    ('The archive was called styles.dat.exe, sadly.', ['styles.dat.exe']),
    ('Attackers edited .user.ini on the server.', ['.user.ini']),
    ('See README-IF-YOU-SEE-THIS-YOUVE-BEEN-HACKED.TXT for details.', ['README-IF-YOU-SEE-THIS-YOUVE-BEEN-HACKED.TXT']),
])
def test_file_names_are_found(text, expected):
    assert files(f'<p>{text}</p>') == expected


@pytest.mark.parametrize('text', [
    'It targets Node.js and ASP.NET apps built with Next.js.',
    'Uploads a .jsp web shell or a .exe payload.',
    'Visit example.com or evil.net for more.',
    'Download from https://example.com/reports/annual.pdf today.',
    'Mail admin@report.pdf about it.',
    'Quoted code used %sconfig.zip as a template.',
    'Version 1.2.3 shipped on 10.0.0.1 today.',
])
def test_things_that_are_not_file_names_are_skipped(text):
    assert files(f'<p>{text}</p>') == []


def test_case_variants_collapse_and_order_is_kept():
    assert files('<p>Run second.exe then FIRST.exe and first.EXE again.</p>') == ['second.exe', 'FIRST.exe']


def test_common_tools_are_marked_apart_from_samples():
    result = extract_iocs('<p>cmd.exe launched dropper.exe and PowerShell.exe</p>')['files']
    assert {item['value']: item['common'] for item in result} == {'cmd.exe': True, 'dropper.exe': False, 'PowerShell.exe': True}


def test_math_and_empty_articles_yield_nothing():
    assert extract_iocs('<p><span class="article-math" data-tex="x">%s</span></p>' % MD5) == EMPTY
    assert extract_iocs('') == EMPTY
    assert extract_iocs(None) == EMPTY


def test_markdown_lists_samples_before_common_tools():
    text = iocs_markdown(extract_iocs(f'<p>{MD5} cmd.exe dropper.exe</p>'))
    assert f'- MD5: `{MD5}`' in text
    assert text.index('dropper.exe') < text.index('Common tools') < text.index('cmd.exe')
    assert iocs_markdown(EMPTY) == ''


@pytest.mark.parametrize('text,expected', [
    ('Exploits CVE-2026-20127 and cve-2025-55182.', ['CVE-2026-20127', 'CVE-2025-55182']),
    ('A long one: CVE-2026-109618.', ['CVE-2026-109618']),
    ('Printed with non-breaking hyphens: CVE\u20112026\u20110257.', ['CVE-2026-0257']),
    ('Twice CVE-2026-20127, CVE-2026-20127.', ['CVE-2026-20127']),
])
def test_cves_are_found_and_normalised(text, expected):
    assert values(f'<p>{text}</p>', 'cves') == expected


@pytest.mark.parametrize('text', ['CVE-2026-123', 'CVE-26-12345', 'XCVE-2026-12345', 'CVE-2026-12345-extra', 'CVE-1850-12345'])
def test_near_cves_are_skipped(text):
    assert values(f'<p>{text}</p>', 'cves') == []


@pytest.mark.parametrize('text,expected', [
    ('Uses T1059.001 then T1566.', ['T1059.001', 'T1566']),
    ('Written T1059/001 by some vendors.', ['T1059.001']),
    ('Tactic TA0001 and ICS technique T0855.', ['TA0001', 'T0855']),
    ('Twice T1190, T1190.', ['T1190']),
    ('ATLAS lists AML.T0043, AML.T0051.000 and AML.TA0002.', ['AML.T0043', 'AML.T0051.000', 'AML.TA0002']),
])
def test_attack_ids_are_found_and_normalised(text, expected):
    assert values(f'<p>{text}</p>', 'techniques') == expected


@pytest.mark.parametrize('text', ['The T1000 from Terminator.', 'Actor TA453 and TA505 again.', 'Model T1234X and T1234-B.', 'Lowercase t1059 is prose.', 'Seen in AT1059 logs.', 'T2024 and T999.', 'Bare T0043 without its prefix.'])
def test_near_attack_ids_are_skipped(text):
    assert values(f'<p>{text}</p>', 'techniques') == []


def test_ids_found_only_in_links_count():
    html = '<p><a href="https://attack.mitre.org/techniques/T1059/001/">PowerShell</a> and <a href="https://nvd.nist.gov/vuln/detail/CVE-2026-0257">a flaw</a>, <a href="https://attack.mitre.org/tactics/TA0002/">Execution</a>.</p>'
    result = extract_iocs(html)
    assert [item['value'] for item in result['techniques']] == ['T1059.001', 'TA0002']
    assert [item['value'] for item in result['cves']] == ['CVE-2026-0257']


def test_unrelated_links_add_nothing():
    assert extract_iocs('<p><a href="https://example.com/T1059">a post</a></p>') == EMPTY


def test_markdown_includes_cves_and_techniques():
    text = iocs_markdown(extract_iocs('<p>CVE-2026-0257 and T1059.001</p>'))
    assert '### CVEs' in text and '- CVE-2026-0257' in text
    assert '### MITRE ATT&CK' in text and '- T1059.001' in text


@pytest.mark.parametrize('identifier,name', [
    ('T1059', 'Command and Scripting Interpreter'),
    ('T1059.001', 'Command and Scripting Interpreter: PowerShell'),
    ('T1566.002', 'Phishing: Spearphishing Link'),
    ('TA0001', 'Initial Access'),
    ('AML.T0043', 'Craft Adversarial Data'),
])
def test_ids_carry_mitre_names(identifier, name):
    assert extract_iocs(f'<p>{identifier}</p>')['techniques'] == [{'value': identifier, 'name': name}]


def test_slash_form_gets_the_same_name():
    assert extract_iocs('<p>T1059/001</p>')['techniques'][0]['name'] == 'Command and Scripting Interpreter: PowerShell'


def test_retired_ids_keep_their_name_and_are_flagged():
    assert extract_iocs('<p>T1086</p>')['techniques'] == [{'value': 'T1086', 'name': 'PowerShell', 'retired': True}]


def test_unknown_id_still_listed_without_a_name():
    assert extract_iocs('<p>T1999.999</p>')['techniques'] == [{'value': 'T1999.999'}]


def test_markdown_puts_names_next_to_ids():
    text = iocs_markdown(extract_iocs('<p>T1059.001 T1086 T1999</p>'))
    assert '- T1059.001 Command and Scripting Interpreter: PowerShell' in text
    assert '- T1086 PowerShell (retired by MITRE)' in text
    assert '- T1999\n' in text


BUNDLE = {'objects': [
    {'type': 'x-mitre-collection', 'x_mitre_version': '20.0'},
    {'type': 'attack-pattern', 'name': 'Phishing', 'external_references': [{'source_name': 'mitre-attack', 'external_id': 'T1566'}]},
    {'type': 'attack-pattern', 'name': 'Spearphishing Link', 'external_references': [{'source_name': 'capec', 'external_id': 'CAPEC-1'}, {'source_name': 'mitre-attack', 'external_id': 'T1566.002'}]},
    {'type': 'attack-pattern', 'name': 'Old One', 'revoked': True, 'external_references': [{'source_name': 'mitre-attack', 'external_id': 'T1086'}]},
    {'type': 'x-mitre-tactic', 'name': 'Execution', 'external_references': [{'source_name': 'mitre-attack', 'external_id': 'TA0002'}]},
    {'type': 'attack-pattern', 'name': 'Bad <img src=x>', 'external_references': [{'source_name': 'mitre-attack', 'external_id': 'T1001'}]},
    {'type': 'attack-pattern', 'name': 'Bad id', 'external_references': [{'source_name': 'mitre-attack', 'external_id': 'not-an-id'}]},
    {'type': 'malware', 'name': 'Ignored', 'external_references': [{'source_name': 'mitre-attack', 'external_id': 'T1999'}]},
]}


def test_updater_reads_stix_and_rejects_bad_values():
    from intel_sourcerer.update_attack_names import build, bundle_version, stix_entries
    result = build(stix_entries(BUNDLE))
    assert result == {
        'names': {'T1086': 'Old One', 'T1566': 'Phishing', 'T1566.002': 'Phishing: Spearphishing Link', 'TA0002': 'Execution'},
        'retired': ['T1086'],
    }
    assert bundle_version(BUNDLE) == '20.0'


def test_updater_reads_atlas_with_parent_names():
    from intel_sourcerer.update_attack_names import atlas_entries
    data = {'matrices': [{'tactics': [{'id': 'AML.TA0002', 'name': 'Reconnaissance'}], 'techniques': [{'id': 'AML.T0051', 'name': 'LLM Prompt Injection'}, {'id': 'AML.T0051.000', 'name': 'Direct'}]}]}
    assert ('AML.T0051.000', 'LLM Prompt Injection: Direct', False) in atlas_entries(data)
    assert ('AML.TA0002', 'Reconnaissance', False) in atlas_entries(data)


def test_live_names_beat_retired_and_earlier_sources_win():
    from intel_sourcerer.update_attack_names import build
    assert build([('T1', 'Old', True), ('T1', 'New', False)]) == {'names': {'T1': 'New'}, 'retired': []}
    assert build([('T2', 'First', False), ('T2', 'Second', False)]) == {'names': {'T2': 'First'}, 'retired': []}


def test_bundled_lookup_is_sane():
    data = __import__('json').loads(__import__('pathlib').Path(__import__('intel_sourcerer.iocs').iocs.__file__).with_name('attack_names.json').read_text())
    assert len(data['names']) > 1000 and set(data['retired']) <= set(data['names'])
    assert all('<' not in name and len(name) <= 200 for name in data['names'].values())
