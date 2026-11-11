from intel_sourcerer.sessions import clean_notes


def test_notes_keep_tables_but_drop_their_attributes():
    html = '<table border="1" onclick="x()"><tbody><tr><th style="color:red">IOC</th><th>Type</th></tr><tr><td>1.2.3.4</td><td>IP → C2</td></tr></tbody></table>'
    assert clean_notes(html) == '<table><tbody><tr><th>IOC</th><th>Type</th></tr><tr><td>1.2.3.4</td><td>IP → C2</td></tr></tbody></table>'
