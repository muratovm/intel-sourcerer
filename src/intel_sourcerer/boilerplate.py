"""Remove site furniture around an article: tag lists, share bars, related-article cards.

Rules match exact labels and link-only blocks, because a wrongly removed paragraph
is worse than a leftover tag list. Headings like "Related work" stay for that reason.
"""
import re

from bs4 import BeautifulSoup

HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
# Everything from one of these labels to the end of the page belongs to the site.
TAIL_LABELS = {
    'related', 'related articles', 'related posts', 'related reading', 'related stories',
    'related content', 'related blogs', 'related resources', 'you may also like',
    'you might also like', 'recommended', 'recommended for you', 'recommended reading',
    'read next', 'keep reading', 'more from this author', 'more articles', 'more posts',
    'more stories', 'latest posts', 'latest articles', 'explore', 'featured research',
}
# These sit anywhere: the label plus the list or short lines straight after it.
SECTION_LABELS = {
    'tags', 'tagged', 'tagged with', 'topics', 'related topics', 'popular topics',
    'categories', 'category', 'posted in', 'filed under', 'share', 'share this',
    'share this post', 'share this article', 'share on', 'follow', 'follow us',
    'content types', 'products and services', 'threats intelligence', 'related products',
    'table of contents',
}
# Real names are worth keeping; only a one-word organisation credit is noise.
AUTHOR_LABELS = {'author', 'authors'}
# Real content that may follow site furniture, so a tail cut stops here.
KEEP_LABELS = {
    'footnotes', 'notes', 'references', 'bibliography', 'endnotes', 'sources',
    'acknowledgements', 'acknowledgments', 'appendix',
}
INLINE_TAG_LINE = re.compile(r'^(tags?|tagged( with)?|topics?|categor(y|ies)|posted in|filed under)\s*:', re.I)
CALL_TO_ACTION = re.compile(r'^(contact (us|mandiant|sales)\b.*|.*get a demo|request a demo|sign up.*|subscribe.*|view open roles)$', re.I)
SITE_LINK = re.compile(r'^(←\s*)?(back to .{1,40}|view more|see more|see all.{0,30}|view all.{0,30})$', re.I)
KEEP_LINK = re.compile(r'^(read|download|watch|see|view|get)\b', re.I)
AUTHOR_CARD_LINK = re.compile(r'^see (all )?posts by this author$', re.I)
CARD_META = re.compile(r'\b\d+[- ]min(ute)?s?[- ]read\b', re.I)
NEXT_ARTICLE = re.compile(r'\bNext:\s', re.I)
LANGUAGES = {'english', 'español', 'espanol', 'français', 'deutsch', 'português', 'italiano', '中文', '日本語', '한국어', 'русский', 'العربية'}
# Above this share, the rules have likely misread the page, so it stays as it was.
MAX_REMOVED = 0.5
# A "Related" heading this far from the end is probably not the end of the page.
MAX_TAIL = 0.3
LONG_PARAGRAPH = 200


def _text(node):
    if hasattr(node, 'get_text'):
        return ' '.join(node.get_text(' ', strip=True).split())
    return ' '.join(str(node).split())


def _label(node):
    return re.sub(r'[^\w\s&]', '', _text(node).casefold()).strip()


def _is_block(node):
    return getattr(node, 'name', None) is not None or bool(str(node).strip())


def _blocks(root):
    return [node for node in root.contents if _is_block(node)]


def _is_label(node):
    return getattr(node, 'name', None) in HEADINGS | {'p', 'div'} and len(_text(node)) <= 60


def _link_only(node):
    """Every word in the block is a link, so it points elsewhere instead of saying anything."""
    if getattr(node, 'name', None) is None:
        return False
    text = _text(node)
    if not text:
        return False
    linked = ' '.join(_text(link) for link in node.find_all('a'))
    return len(''.join(linked.split())) >= len(''.join(text.split())) * 0.9


def _placeholder(node):
    return getattr(node, 'name', None) == 'blockquote' and _text(node).startswith(('Image placeholder:', 'Embedded media placeholder:', 'Video placeholder:'))


def _empty(node):
    if getattr(node, 'name', None) is None:
        return not str(node).strip()
    return not _text(node) and node.name not in {'br', 'hr', 'img', 'figure', 'video', 'audio', 'iframe'} and not node.find(['img', 'figure', 'video', 'audio', 'iframe'])


def _nav_list(node):
    """Site navigation: mostly short links, including nested menus."""
    if getattr(node, 'name', None) not in {'ul', 'ol'}:
        return False
    links = node.find_all('a')
    if len(links) < 5 or any(len(_text(link).split()) > 6 for link in links):
        return False
    linked = sum(len(''.join(_text(link).split())) for link in links)
    return linked >= len(''.join(_text(node).split())) * 0.6


def _is_tag_item(item):
    """A "Categories: x, y" entry in a byline; an icon placeholder may come first."""
    words = [child for child in item.find_all(recursive=False) if child.name != 'blockquote']
    return bool(words) and len(_text(item)) <= 300 and INLINE_TAG_LINE.match(_text(words[0])) is not None


def _short_link_list(node):
    if getattr(node, 'name', None) not in {'ul', 'ol'}:
        return False
    items = node.find_all('li', recursive=False)
    return bool(items) and all(_link_only(item) and len(_text(item).split()) <= 6 for item in items)


def _card(node):
    """A teaser for another article: a linked title with its own reading time."""
    return CARD_META.search(_text(node)) is not None and len(_text(node)) <= 300 and (_link_only(node) or bool(node.find('a')))


def _level(node):
    return int(node.name[1]) if getattr(node, 'name', None) in HEADINGS else None


def _section_end(blocks, start):
    """A label owns the list or links right after it, and never any prose."""
    end = start + 1
    while end < len(blocks):
        node = blocks[end]
        if _level(node) or _label(node) in KEEP_LABELS:
            break
        # Share bars and teaser cards hold plain text too; a tag list under "Topics" is all links.
        label = _label(blocks[start])
        any_list = label.startswith('share') or label in TAIL_LABELS
        is_list = node.name in {'ul', 'ol'} and len(_text(node)) <= 600 and (any_list or _link_only(node))
        is_links = node.name in {'p', 'div'} and _link_only(node) and len(_text(node)) <= 200
        blank_image = _placeholder(node) and 'No description supplied' in _text(node)
        if not (is_list or is_links or blank_image or _empty(node)):
            break
        end += 1
    return end


def _mark_sections(blocks, doomed):
    total = len(blocks)
    sizes = [len(_text(node)) for node in blocks]
    all_chars = sum(sizes) or 1
    for index, node in enumerate(blocks):
        if index in doomed or not hasattr(node, 'get_text') or not _is_label(node):
            continue
        label = _label(node)
        if label in AUTHOR_LABELS:
            following = blocks[index + 1] if index + 1 < total else None
            if following is not None and len(_text(following).split()) == 1 and not _level(following):
                doomed.update((index, index + 1))
        elif label in SECTION_LABELS:
            end = _section_end(blocks, index)
            # A label followed by prose is a real section that happens to share the word.
            if end == index + 1 and end < total and not _level(blocks[end]) and _label(blocks[end]) not in KEEP_LABELS:
                continue
            doomed.update(range(index, end))
        elif label in TAIL_LABELS:
            end = index
            while end < total and _label(blocks[end]) not in KEEP_LABELS:
                end += 1
            if (index + 1) / total >= 0.5 and sum(sizes[index:end]) / all_chars <= MAX_TAIL:
                doomed.update(range(index, end))
            else:
                end = _section_end(blocks, index)
                if end == index + 1 and end < total and not _level(blocks[end]):
                    continue
                doomed.update(range(index, end))
                if end < total and SITE_LINK.match(_text(blocks[end])):
                    doomed.add(end)


def _mark_lines(blocks, doomed):
    first_long = next((i for i, node in enumerate(blocks) if getattr(node, 'name', None) == 'p' and len(_text(node)) >= LONG_PARAGRAPH), len(blocks))
    for index, node in enumerate(blocks):
        if index in doomed or not hasattr(node, 'get_text'):
            continue
        text = _text(node)
        if _empty(node):
            doomed.add(index)
        elif node.name == 'ul' and re.match(r'^published\b', text, re.I) and re.search(r'\b(topics?|category|team)\b', text, re.I) and len(text) <= 400:
            doomed.add(index)
        elif index < first_long and (_short_link_list(node) or _nav_list(node)):
            doomed.add(index)
        elif len(text) <= 120 and text.endswith('Related Products'):
            # Product promos alternate an icon with a linked name, until the article starts.
            end = index + 1
            while end < len(blocks) and not _level(blocks[end]) and (_placeholder(blocks[end]) or len(_text(blocks[end])) <= 60 or _short_link_list(blocks[end]) or _empty(blocks[end])):
                end += 1
            doomed.update(range(index, end))
        elif len(text) <= 200 and node.name in {'p', 'div'} and INLINE_TAG_LINE.match(text):
            doomed.add(index)
        elif len(text) <= 60 and set(text.casefold().split()) <= LANGUAGES:
            doomed.add(index)
        elif node.name == 'p' and _link_only(node) and SITE_LINK.match(text):
            doomed.add(index)
        elif node.name == 'p' and NEXT_ARTICLE.search(text) and _link_only(node) and len(text) <= 300:
            doomed.add(index)
        elif node.name == 'p' and text.endswith('View open roles') and len(text) <= 120:
            doomed.add(index)
        elif node.name == 'pre' and re.match(r'^@(misc|article|inproceedings|techreport)\s*\{', text):
            doomed.add(index)
            if index and _is_label(blocks[index - 1]) and re.search(r'\b(bibtex|cit(e|ation))\b', _text(blocks[index - 1]), re.I):
                doomed.add(index - 1)
        elif node.name == 'p' and _link_only(node) and CALL_TO_ACTION.match(text):
            # A promo reads heading, tagline, button.
            doomed.add(index)
            for back in (index - 1, index - 2):
                if back >= 0 and len(_text(blocks[back])) <= 100 and (getattr(blocks[back], 'name', None) in {'h5', 'h6'} or (back == index - 1 and not _link_only(blocks[back]))):
                    doomed.add(back)
        elif node.name == 'p' and AUTHOR_CARD_LINK.match(text):
            # The name and role stay as a credit; the link and blank photo go.
            doomed.add(index)
            if index + 1 < len(blocks) and _placeholder(blocks[index + 1]) and 'No description supplied' in _text(blocks[index + 1]):
                doomed.add(index + 1)
            for back in range(index - 1, max(index - 3, -1), -1):
                if getattr(blocks[back], 'name', None) == 'h1':
                    blocks[back].name = 'h4'
                    break


def _mark_cards(blocks, doomed):
    """Runs of article teasers, with their thumbnails, wherever they fall."""
    for index, node in enumerate(blocks):
        if index in doomed or not hasattr(node, 'get_text') or not _card(node):
            continue
        neighbours = [blocks[i] for i in (index - 2, index + 2) if 0 <= i < len(blocks)]
        if any(_card(other) for other in neighbours) or (node.name in {'ul', 'ol'} and len(node.find_all('li', recursive=False)) >= 2):
            doomed.add(index)
            if index and _placeholder(blocks[index - 1]):
                doomed.add(index - 1)
            if index + 1 < len(blocks) and _placeholder(blocks[index + 1]):
                doomed.add(index + 1)


def _mark_trailing(blocks, doomed):
    """Tag lists and stray links left dangling after the last real text."""
    index = len(blocks) - 1
    while index >= 0:
        node = blocks[index]
        cut_below = index + 1 in doomed or index + 1 == len(blocks)
        if index in doomed or _empty(node):
            doomed.add(index)
        elif getattr(node, 'name', None) in {'ul', 'ol'} and _short_link_list(node) and all(len(_text(item).split()) <= 4 for item in node.find_all('li', recursive=False)):
            doomed.add(index)
        elif _placeholder(node) and cut_below and ('No description supplied' in _text(node) or index + 1 < len(blocks)):
            doomed.add(index)
        elif getattr(node, 'name', None) == 'p' and _link_only(node) and len(_text(node).split()) <= 4 and not KEEP_LINK.match(_text(node)):
            doomed.add(index)
        else:
            break
        index -= 1


def strip_boilerplate(soup):
    """Remove furniture in place and return the text removed, block by block."""
    root = soup
    while True:
        blocks = _blocks(root)
        if len(blocks) == 1 and getattr(blocks[0], 'name', None) in {'div', 'article', 'section', 'main'}:
            root = blocks[0]
        else:
            break
    tag_items = [item for item in root.find_all('li') if _is_tag_item(item)]
    doomed = set()
    _mark_sections(blocks, doomed)
    _mark_lines(blocks, doomed)
    _mark_cards(blocks, doomed)
    # Second pass: tag lists can sit just above an author section that has now gone.
    _mark_trailing(blocks, doomed)
    # Footnotes keep their place; furniture just above them still goes.
    keep_from = next((i for i in range(len(blocks) - 1, -1, -1) if _label(blocks[i]) in KEEP_LABELS and i not in doomed), None)
    if keep_from is not None:
        index = keep_from - 1
        while index >= 0 and (index in doomed or _short_link_list(blocks[index]) or _empty(blocks[index])):
            doomed.add(index)
            index -= 1
    removed_chars = sum(len(_text(blocks[i])) for i in doomed)
    all_chars = sum(len(_text(node)) for node in blocks)
    if all_chars and removed_chars / all_chars > MAX_REMOVED:
        return []
    removed = []
    for item in tag_items:
        removed.append(_text(item))
        item.decompose()
    for index in sorted(doomed):
        node = blocks[index]
        if _text(node):
            removed.append(_text(node))
        if hasattr(node, 'decompose'):
            node.decompose()
        else:
            node.extract()
    return removed


def title_without_category(title, html):
    """Listing cards can run a category into the title; the article's own heading settles it."""
    if not html:
        return title
    heading = BeautifulSoup(html, 'html.parser').find('h1')
    plain = ' '.join((title or '').split())
    own = _text(heading) if heading else ''
    if len(own.split()) < 3 or plain == own or not plain.endswith(' ' + own):
        return title
    prefix = plain[:-len(own)].strip()
    # Punctuation means a deliberate title, like "Part 2: ..."; a category is just a word or few.
    if len(prefix.split()) > 4 or not prefix[0].isupper() or re.search(r'[^\w\s&-]', prefix):
        return title
    return own
