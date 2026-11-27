from bs4 import BeautifulSoup

from intel_sourcerer.content import arrange_media, drop_above_title

HERO = "https://example.com/hero.jpg"


def test_hero_image_above_the_title_survives():
    soup = BeautifulSoup(
        "<blockquote><p><strong>Image placeholder: </strong>No description supplied</p>"
        f'<p><a href="{HERO}">View original image</a></p></blockquote>'
        "<p>Research</p><h1>Weekly report</h1><p>Body text.</p>",
        "html.parser",
    )
    drop_above_title(soup)
    arrange_media(soup, "Weekly report", "https://example.com/post/")
    assert soup.find("h1") is None
    assert "Research" not in soup.get_text()
    figure = soup.find("figure")
    assert figure is not None and figure["data-media-url"] == HERO
    assert "Body text." in soup.get_text()


def test_without_a_title_nothing_is_dropped():
    soup = BeautifulSoup("<p>Kicker</p><p>Body</p>", "html.parser")
    drop_above_title(soup)
    assert soup.get_text() == "KickerBody"
