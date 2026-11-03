import re

import pytest

from intel_sourcerer.config import PublisherSource
from intel_sourcerer.source_view import render_sources_list
from intel_sourcerer.templates import load_template


def header(template):
    return re.search(r'<header class="builder-header">.*?</header>', load_template(template), re.S).group(0)


PAGES = {
    'workspace/home.html': '/',
    'workspace/queue-builder.html': '/?view=builder',
    'library/index.html': '/library',
    'topics/index.html': '/topics',
    'topics/topic.html': '/topics',
    'sources/index.html': '/sources',
}


@pytest.mark.parametrize('template,active', PAGES.items())
def test_each_page_has_the_shared_header_with_its_link_marked(template, active):
    drawn = header(template)
    assert re.findall(r'href="([^"]+)"', drawn) == ['/', '/?view=builder', '/topics', '/library', '/sources']
    assert re.findall(r'aria-current="page" href="([^"]+)"', drawn) == [active]
    assert drawn.replace(' class="active" aria-current="page"', '') == header('auth/account-page.html')


def test_account_page_marks_no_link():
    assert 'aria-current' not in header('auth/account-page.html')


def test_sources_list_does_not_repeat_a_legacy_link():
    source = PublisherSource(
        id='a', name='A', source_type='security_vendor', parent_url='https://example.com/new/',
        rss_url='https://example.com/rss', legacy_parent_url='https://example.com/old/', enabled=True,
    )
    page = render_sources_list([source])
    assert 'https://example.com/new/' in page and 'https://example.com/old/' not in page and 'Legacy' not in page
