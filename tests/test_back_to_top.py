from bs4 import BeautifulSoup

from intel_sourcerer.boilerplate import strip_boilerplate, strip_named_furniture

ARROW = (
    "<blockquote><p><strong>Image placeholder: </strong>No description supplied</p>"
    '<p><a href="https://example.com/back_arrow.svg">View original image</a></p></blockquote>'
)
PROSE = "<p>" + "Attackers moved laterally through the network. " * 8 + "</p>"


def test_everything_after_the_last_go_up_link_goes():
    soup = BeautifulSoup(
        PROSE + PROSE + ARROW + '<p><a href="#single-post">GO UP</a></p>'
        "<h3>BLOGS AND PUBLICATIONS</h3>" + ARROW + "<p><span>February 17, 2020</span></p>"
        "<h3>An older post</h3><h2>We value your privacy!</h2><p>ACCEPT</p>",
        "html.parser",
    )
    strip_boilerplate(soup)
    text = soup.get_text(" ")
    assert "Attackers moved" in text
    for gone in ("GO UP", "BLOGS", "older post", "privacy", "ACCEPT"):
        assert gone not in text
    assert soup.find("blockquote") is None


def test_back_to_top_links_between_sections_keep_the_prose_after_them():
    soup = BeautifulSoup(
        "<h2>One</h2>" + PROSE + '<p><a href="#top">Back to top</a></p><h2>Two</h2>' + PROSE,
        "html.parser",
    )
    strip_boilerplate(soup)
    assert soup.get_text(" ").count("Attackers moved") == 16
    assert soup.find("h2", string="Two") is not None


def test_named_page_furniture_is_removed():
    soup = BeautifulSoup(
        "<body>" + PROSE * 4
        + '<div class="back-to-top"><a href="#">GO UP</a></div>'
        + '<div class="aside-box"><h3>BLOGS AND PUBLICATIONS</h3></div>'
        + '<div class="cookies-notice"><p>We value your privacy</p></div>'
        + '<div class="popup" id="subscribe-us-popup"><h2>SUBSCRIBE</h2></div></body>',
        "html.parser",
    )
    strip_named_furniture(soup.body)
    text = soup.get_text(" ")
    assert "Attackers moved" in text
    for gone in ("GO UP", "BLOGS", "privacy", "SUBSCRIBE"):
        assert gone not in text
